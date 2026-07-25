"""etl.py — Stage A: explode household rows into a person-level table.

Reads data/Nassau_Unrolled.csv + data/Suffolk_Unrolled.csv, parses
household_detail into one row per registered voter plus one row per ballot
they ever cast (the unrolled per-election vote history), geocodes households
via the TIGER interpolator from build/build.py, joins FEC + NY BOE donation
records (recovered from the dist/ payloads when the gitignored caches are
absent), and writes:

    model/artifacts/persons.parquet          one row per voter
    model/artifacts/donor_committees.parquet (person_row, committee, source)
    model/artifacts/elections.parquet        (person_row, year, etype, method)
                                             one row per ballot cast

Usage:
    python model/etl.py                 # full build
    python model/etl.py --county NASSAU --city "GLEN COVE"   # smoke-test subset
"""
import argparse
import base64
import gzip
import hashlib
import json
import sys
from array import array
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "build"))

import config as C
from build import Geocoder, extract_tiger, parse_household  # noqa: E402  (from build/build.py)

# Election-history vocabulary: the single chars parse_household emits
# (build.BALLOT_TYPE_MAP / BALLOT_METHOD_MAP). Stored as parquet categoricals.
ETYPE_CATS = ["G", "P"]                              # general, primary
METHOD_CATS = ["E", "V", "A", "F", "D", "M", "O"]    # poll site, early, absentee,
                                                     # federal, affidavit, mail, other
_ETYPE_CODE = {c: i for i, c in enumerate(ETYPE_CATS)}
_METHOD_CODE = {c: i for i, c in enumerate(METHOD_CATS)}


# ------------------------------------------------------------------ loading

def load_households(county: str | None, city: str | None) -> pd.DataFrame:
    frames = []
    for path in C.VOTER_SOURCES:
        print(f"  reading {path.name}...")
        frames.append(pd.read_csv(path))
    df = pd.concat(frames, ignore_index=True)
    if county:
        df = df[df["county"].str.upper() == county.upper()]
    if city:
        df = df[df["city"].astype(str).str.upper() == city.upper()]
    df = df.reset_index(drop=True)
    # Match build.py conventions: address/zip as clean strings ("17B" stays as-is,
    # 11797.0 -> "11797", null/NaN -> ""). Rows without a usable address are KEPT
    # (they still carry voters) but won't geocode.
    def clean_str(s: pd.Series) -> pd.Series:
        out = s.astype(str).str.strip()
        out = out.str.replace(r"\.0$", "", regex=True)
        return out.where(~out.str.lower().isin(["nan", "null", "none", "<na>", ""]), "")
    df["address_number"] = clean_str(df["address_number"])
    df["zip_code"] = clean_str(df["zip_code"])
    print(f"  {len(df):,} household rows")
    return df


def geocode_households(df: pd.DataFrame) -> pd.DataFrame:
    """Interpolate household lat/lon from TIGER; cached because it dominates runtime."""
    cache = C.ARTIFACTS / f"geocode_cache_{len(df)}.parquet"
    if cache.exists():
        cached = pd.read_parquet(cache)
        if len(cached) == len(df):
            print(f"  using geocode cache {cache.name}")
            df["lon"], df["lat"] = cached["lon"].to_numpy(), cached["lat"].to_numpy()
            return df
    print("Building geocoder indexes...")
    geocoders = {c: Geocoder(extract_tiger(c)) for c in df["county"].unique()}
    print(f"Geocoding {len(df):,} households...")
    lons, lats = [], []
    for county, addr, street, zip5 in zip(
        df["county"], df["address_number"], df["street_name"], df["zip_code"]
    ):
        point = geocoders[county].geocode(addr, street, zip5) if addr else None
        lons.append(round(point[0], 5) if point else np.nan)
        lats.append(round(point[1], 5) if point else np.nan)
    df["lon"], df["lat"] = lons, lats
    hits = df["lon"].notna().sum()
    print(f"  geocoded {hits:,}/{len(df):,} ({100 * hits / len(df):.1f}%)")
    df[["lon", "lat"]].to_parquet(cache, index=False)
    return df


# ---------------------------------------------------------------- donations

def election_day(year: int) -> date:
    """First Tuesday after the first Monday of November."""
    first = date(year, 11, 1)
    first_monday = 1 + (7 - first.weekday()) % 7
    return date(year, 11, first_monday + 1)


def _filter_records(table: dict, cutoff: date) -> tuple[dict, int, int]:
    """Keep only donation records dated strictly before cutoff.

    Dateless (or unparseable-date) records are dropped too: they cannot be
    placed relative to the cutoff, and keeping them would leak post-election
    donations into the as-of feature set.
    """
    kept_tbl, dropped_post, dropped_dateless = {}, 0, 0
    for key, val in table.items():
        kept = []
        for r in (val.get("c") or []):
            try:
                y, m, d = (int(x) for x in (r.get("date") or "").split("-"))
                rec_date = date(y, m, d)
            except ValueError:
                dropped_dateless += 1
                continue
            if rec_date >= cutoff:
                dropped_post += 1
                continue
            kept.append(r)
        if kept:
            kept_tbl[key] = {"c": kept}
    return kept_tbl, dropped_post, dropped_dateless


def load_donations(cutoff: date) -> tuple[dict, dict]:
    """Return (fec, nyboe) donor dicts keyed by 'NAME|CITY|ZIP5', restricted
    to records dated before `cutoff` (the target general's election day) so
    donation features and co-donor edges are as-of the prediction target.

    Prefers the raw gitignored caches; falls back to the committed dist/
    payloads (which contain only confirmed matches — exactly what we want).
    """
    if C.FEC_CACHE.exists():
        raw = json.loads(C.FEC_CACHE.read_text())
        fec = {k: {"c": v["confirmed"]} for k, v in raw.items() if v.get("confirmed")}
        print(f"  FEC: {len(fec):,} confirmed donors (from data/fec_cache.json)")
    else:
        payload = json.loads(gzip.decompress(base64.b64decode(C.COUNTY_B64.read_text())))
        fec = payload.get("fec_donations", {})
        print(f"  FEC: {len(fec):,} confirmed donors (from dist/nassau-data.b64)")

    if C.NYBOE_CACHE.exists():
        raw = json.loads(C.NYBOE_CACHE.read_text())
        nyboe = {k: {"c": v["confirmed"]} for k, v in raw.items() if v.get("confirmed")}
        print(f"  NYBOE: {len(nyboe):,} confirmed donors (from data/nyboe_cache.json)")
    else:
        nyboe = json.loads(gzip.decompress(base64.b64decode(C.NYBOE_B64.read_text())))
        print(f"  NYBOE: {len(nyboe):,} confirmed donors (from dist/nyboe-data.b64)")

    out = []
    for name, table in (("FEC", fec), ("NYBOE", nyboe)):
        filtered, post, dateless = _filter_records(table, cutoff)
        print(f"  {name}: kept {len(filtered):,} donors with records before {cutoff} "
              f"(dropped {post:,} post-cutoff + {dateless:,} dateless records)")
        out.append(filtered)
    return out[0], out[1]


def donation_features(key: str, fec: dict, nyboe: dict, ref: date):
    """Aggregate confirmed donation records for one voter key."""
    out = {}
    committees = []
    for src, table in (("fec", fec), ("nyboe", nyboe)):
        recs = (table.get(key) or {}).get("c") or []
        total = sum(r["amount"] or 0 for r in recs)
        out[f"{src}_n"] = len(recs)
        out[f"{src}_total"] = total
        dates = [r["date"] for r in recs if r.get("date")]
        if dates:
            y, m, d = (int(x) for x in max(dates).split("-"))
            out[f"{src}_recency_days"] = (ref - date(y, m, d)).days
        else:
            out[f"{src}_recency_days"] = np.nan
        for r in recs:
            committees.append((r["committee"] or "", src, r["amount"] or 0))
    dem_conduit = sum(a for c, _, a in committees if c.upper() in C.DEM_CONDUITS)
    rep_conduit = sum(a for c, _, a in committees if c.upper() in C.REP_CONDUITS)
    out["dem_conduit_total"] = dem_conduit
    out["rep_conduit_total"] = rep_conduit
    out["n_committees"] = len({c for c, _, _ in committees})
    out["has_donation"] = int(bool(committees))
    return out, committees


# ------------------------------------------------------------------ explode

def explode_persons(df: pd.DataFrame, fec: dict, nyboe: dict, cutoff: date
                    ) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    ref = cutoff        # donation recency measured back from the target election
    hh_cols = [
        "county", "town", "city", "zip_code", "election_district",
        "legislative_district", "congressional_district", "senate_district",
        "assembly_district", "voters_at_address", "max_vote_count", "min_vote_count",
        "engagement_gap", "oldest_age", "youngest_age", "num_reliable",
        "num_low_engagement", "lon", "lat", "address_number", "street_name",
    ]
    rows, committee_rows = [], []
    # per-ballot history, accumulated in compact typed arrays (~20M records)
    el_prow, el_year = array("i"), array("h")
    el_type, el_method = bytearray(), bytearray()
    skipped = 0
    for hh_row, rec in enumerate(df.itertuples(index=False)):
        people = parse_household(rec.household_detail)
        if not people:
            skipped += 1
            continue
        city_u = str(rec.city).upper().strip()
        zip5 = str(rec.zip_code).strip()
        hh_parties = [p[2] for p in people]
        n = len(people)
        for name, age, party, tier, elections in people:
            letter, digits = tier[0], tier[1:]
            count = int(digits) if digits.isdigit() else 0
            key = f"{name}|{city_u}|{zip5}"
            person_id = int.from_bytes(
                hashlib.blake2b(f"{key}|{rec.county}|{rec.address_number} {rec.street_name}".encode(),
                                digest_size=8).digest(), "big") >> 1
            don, committees = donation_features(key, fec, nyboe, ref)
            # Household party shares computed over the OTHER members only.
            dem_others = sum(1 for p in hh_parties if p in ("DEM", "WOR")) - (1 if party in ("DEM", "WOR") else 0)
            rep_others = sum(1 for p in hh_parties if p in ("REP", "CON")) - (1 if party in ("REP", "CON") else 0)
            blk_others = sum(1 for p in hh_parties if p == "BLK") - (1 if party == "BLK" else 0)
            row = {
                "person_id": person_id,
                "household_row": hh_row,
                "name": name,
                "age": age,
                "party": party,
                "tier_letter": letter,
                "tier_count": count,
                "household_size": n,
                "hh_dem_share_excl": dem_others / (n - 1) if n > 1 else 0.0,
                "hh_rep_share_excl": rep_others / (n - 1) if n > 1 else 0.0,
                "hh_blk_share_excl": blk_others / (n - 1) if n > 1 else 0.0,
                **{c: getattr(rec, c) for c in hh_cols},
                **don,
            }
            rows.append(row)
            prow = len(rows) - 1
            for committee, src, amount in committees:
                committee_rows.append({
                    "person_row": prow, "committee": committee,
                    "source": src, "amount": amount,
                })
            for e_year, e_code in elections:      # e_code: type+method chars, e.g. "GE"
                if not 1900 <= e_year <= 2100:    # guard the int16 year column
                    continue
                el_prow.append(prow)
                el_year.append(e_year)
                el_type.append(_ETYPE_CODE[e_code[0]])
                el_method.append(_METHOD_CODE[e_code[1]])
    # free the big history strings before materializing the persons table
    df.drop(columns=["household_detail"], inplace=True)
    persons = pd.DataFrame(rows)
    donors = pd.DataFrame(committee_rows,
                          columns=["person_row", "committee", "source", "amount"])
    ballots = pd.DataFrame({
        "person_row": np.asarray(el_prow, dtype=np.int32),
        "year": np.asarray(el_year, dtype=np.int16),
        "etype": pd.Categorical.from_codes(
            np.frombuffer(bytes(el_type), dtype=np.int8), categories=ETYPE_CATS),
        "method": pd.Categorical.from_codes(
            np.frombuffer(bytes(el_method), dtype=np.int8), categories=METHOD_CATS),
    })
    if skipped:
        print(f"  {skipped:,} household rows had no parseable people")
    return persons, donors, ballots


def add_derived(persons: pd.DataFrame, elections: pd.DataFrame) -> pd.DataFrame:
    persons["has_geo"] = persons["lat"].notna().astype(np.int8)
    persons["ed_key"] = (persons["county"].astype(str) + "|"
                         + persons["assembly_district"].astype(str) + "|"
                         + persons["election_district"].astype(str))
    # Labels. y_turnout = actually voted in the target general (Phase 2);
    # -1 masks voters not yet 18 by that election (train/eval skip them, they
    # still get scored). The old tier proxy stays as a diagnostic column.
    E = C.TARGET_GENERAL_YEAR
    ref_year = int(C.REF_DATE[:4])          # ages are current as of the export
    voted = np.zeros(len(persons), dtype=bool)
    sel = (elections["year"] == E) & (elections["etype"] == "G")
    voted[elections.loc[sel, "person_row"].to_numpy()] = True
    eligible = (persons["age"] - (ref_year - E)) >= 18
    persons["y_turnout"] = np.where(eligible, voted.astype(np.int8), -1).astype(np.int8)
    persons["y_turnout_tier"] = (persons["tier_count"] >= C.TURNOUT_COUNT_THRESHOLD).astype(np.int8)
    party_class = persons["party"].map(C.PARTY_CLASS)
    party_class = party_class.where(persons["party"] != "BLK", C.PARTY_MASKED)
    persons["y_party"] = party_class.fillna(C.PARTY_OTHER).astype(np.int8)

    # ED context, leave-self-out for registration shares
    g = persons.groupby("ed_key")
    ed_n = g["person_id"].transform("size").astype(np.float64)
    is_dem = persons["y_party"].eq(0).astype(np.float64)
    is_rep = persons["y_party"].eq(1).astype(np.float64)
    is_blk = persons["party"].eq("BLK").astype(np.float64)
    for col, ind in (("ed_dem_share_excl", is_dem), ("ed_rep_share_excl", is_rep),
                     ("ed_blk_share_excl", is_blk)):
        ed_sum = ind.groupby(persons["ed_key"]).transform("sum")
        persons[col] = ((ed_sum - ind) / (ed_n - 1)).where(ed_n > 1, 0.0)
    persons["ed_n_voters"] = ed_n
    persons["ed_mean_tier_count"] = g["tier_count"].transform("mean")
    return persons


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--county", help="restrict to one county (smoke test)")
    ap.add_argument("--city", help="restrict to one city (smoke test)")
    ap.add_argument("--out", type=Path, default=C.PERSONS_PARQUET)
    ap.add_argument("--donors-out", type=Path, default=C.DONOR_COMMITTEES_PARQUET)
    ap.add_argument("--elections-out", type=Path, default=C.ELECTIONS_PARQUET)
    args = ap.parse_args()

    C.ARTIFACTS.mkdir(parents=True, exist_ok=True)
    print("Loading voter files...")
    df = load_households(args.county, args.city)
    df = geocode_households(df)
    cutoff = election_day(C.TARGET_GENERAL_YEAR)
    print(f"Loading donation tables (as-of cutoff {cutoff})...")
    fec, nyboe = load_donations(cutoff)
    print("Exploding households into persons...")
    persons, donors, elections = explode_persons(df, fec, nyboe, cutoff)
    persons = add_derived(persons, elections)

    dup = persons["person_id"].duplicated().sum()
    if dup:
        print(f"  WARNING: {dup} duplicate person_ids (same name+address); keeping all rows")
    expected = df["voters_at_address"].sum()
    print(f"  {len(persons):,} persons (voters_at_address sum = {expected:,})")
    print(f"  party counts:\n{persons['party'].value_counts().head(10).to_string()}")
    elig = persons["y_turnout"] >= 0
    agree = (persons.loc[elig, "y_turnout"] == persons.loc[elig, "y_turnout_tier"]).mean()
    print(f"  y_turnout (voted {C.TARGET_GENERAL_YEAR} general): "
          f"mean {persons.loc[elig, 'y_turnout'].mean():.3f} over {int(elig.sum()):,} eligible "
          f"({int((~elig).sum()):,} under-18-at-election masked); "
          f"tier-proxy agreement {agree:.3f}")
    print(f"  y_party dist: {persons['y_party'].value_counts(normalize=True).round(3).to_dict()}")
    print(f"  donors with committees: {donors['person_row'].nunique():,} "
          f"({donors.shape[0]:,} donation records)")
    if len(elections):
        voters_with_history = elections["person_row"].nunique()
        print(f"  election history: {len(elections):,} ballots across "
              f"{voters_with_history:,} voters "
              f"({100 * voters_with_history / len(persons):.1f}% of persons; "
              f"years {elections['year'].min()}-{elections['year'].max()})")
    else:
        print("  WARNING: no election history parsed — "
              "are VOTER_SOURCES the *_Unrolled files?")

    persons.to_parquet(args.out, index=False)
    donors.to_parquet(args.donors_out, index=False)
    elections.to_parquet(args.elections_out, index=False)
    print(f"Wrote {args.out}, {args.donors_out} and {args.elections_out}")


if __name__ == "__main__":
    main()
