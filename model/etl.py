"""etl.py — Stage A: explode household rows into a person-level table.

Reads data/Nassau.csv + data/Suffolk.csv, parses household_detail into one row
per registered voter, geocodes households via the TIGER interpolator from
build/build.py, joins FEC + NY BOE donation records (recovered from the dist/
payloads when the gitignored caches are absent), and writes:

    model/artifacts/persons.parquet          one row per voter
    model/artifacts/donor_committees.parquet (person_row, committee, source)

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
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "build"))

import config as C
from build import Geocoder, extract_tiger, parse_household  # noqa: E402  (from build/build.py)


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

def load_donations() -> tuple[dict, dict]:
    """Return (fec, nyboe) donor dicts keyed by 'NAME|CITY|ZIP5'.

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
    return fec, nyboe


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

def explode_persons(df: pd.DataFrame, fec: dict, nyboe: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    ref = date.fromisoformat(C.REF_DATE)
    hh_cols = [
        "county", "town", "city", "zip_code", "election_district",
        "legislative_district", "congressional_district", "senate_district",
        "assembly_district", "voters_at_address", "max_vote_count", "min_vote_count",
        "engagement_gap", "oldest_age", "youngest_age", "num_reliable",
        "num_low_engagement", "lon", "lat", "address_number", "street_name",
    ]
    rows, committee_rows = [], []
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
        for name, age, party, tier in people:
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
            for committee, src, amount in committees:
                committee_rows.append({
                    "person_row": len(rows) - 1, "committee": committee,
                    "source": src, "amount": amount,
                })
    persons = pd.DataFrame(rows)
    donors = pd.DataFrame(committee_rows,
                          columns=["person_row", "committee", "source", "amount"])
    if skipped:
        print(f"  {skipped:,} household rows had no parseable people")
    return persons, donors


def add_derived(persons: pd.DataFrame) -> pd.DataFrame:
    persons["has_geo"] = persons["lat"].notna().astype(np.int8)
    persons["ed_key"] = (persons["county"].astype(str) + "|"
                         + persons["assembly_district"].astype(str) + "|"
                         + persons["election_district"].astype(str))
    # Labels
    persons["y_turnout"] = (persons["tier_count"] >= C.TURNOUT_COUNT_THRESHOLD).astype(np.int8)
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
    args = ap.parse_args()

    C.ARTIFACTS.mkdir(parents=True, exist_ok=True)
    print("Loading voter files...")
    df = load_households(args.county, args.city)
    df = geocode_households(df)
    print("Loading donation tables...")
    fec, nyboe = load_donations()
    print("Exploding households into persons...")
    persons, donors = explode_persons(df, fec, nyboe)
    persons = add_derived(persons)

    dup = persons["person_id"].duplicated().sum()
    if dup:
        print(f"  WARNING: {dup} duplicate person_ids (same name+address); keeping all rows")
    expected = df["voters_at_address"].sum()
    print(f"  {len(persons):,} persons (voters_at_address sum = {expected:,})")
    print(f"  party counts:\n{persons['party'].value_counts().head(10).to_string()}")
    print(f"  y_turnout mean: {persons['y_turnout'].mean():.3f}   "
          f"y_party dist: {persons['y_party'].value_counts(normalize=True).round(3).to_dict()}")
    print(f"  donors with committees: {donors['person_row'].nunique():,} "
          f"({donors.shape[0]:,} donation records)")

    persons.to_parquet(args.out, index=False)
    donors.to_parquet(args.donors_out, index=False)
    print(f"Wrote {args.out} and {args.donors_out}")


if __name__ == "__main__":
    main()
