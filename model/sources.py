"""sources.py — where the raw voter data comes from.

Two readers, one output contract (see features_person.assemble):

    from_cache()  local Parquet snapshot of the Supabase tables (config.CACHE).
                  The default: ~30s to read what takes hours over the wire.
    from_csv()    the original data/*_Unrolled.csv export + TIGER geocoding +
                  the dist/*.b64 donation payloads. Kept as the provenance
                  path, so cache-derived numbers can be diffed against the
                  export they came from.

There is deliberately NO reader that queries Supabase directly. Pulling from
the database is refresh_cache.py's job — it is slow and timeout-prone, and
folding it into every ETL run made the pipeline hostage to a 2-minute
statement cap and ~200 rows/s of sustained throughput.

Known differences between the two, measured on Glen Cove (2026-07-27):
  * cache has ~1.28% fewer people — Supabase dedups same-name-same-household
    (Jr/Sr) via ON CONFLICT, which the CSV keeps as separate rows. This also
    removes the blake2b person_id collision problem.
  * cache folds GRE/LBT/WEP into OTH (a CHECK constraint on people.party).
    Both map to y_party = PARTY_OTHER, so no modelling impact.
  * legislative_district has no Supabase column; emitted NA from the cache.
  * geo coverage is equivalent (88.6% cache vs 88.3% CSV) — the same
    addresses fail to interpolate in both.
"""
import base64
import gzip
import hashlib
import json
import sys
from array import array
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

import config as C

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "build"))
from build import (BALLOT_METHOD_MAP, BALLOT_TYPE_MAP, Geocoder,  # noqa: E402
                   extract_tiger, parse_household)

# Election-history vocabulary, derived from the maps build.parse_household
# actually emits rather than restated beside them. dict.fromkeys, not sorted():
# these lists define the integer codes stored in elections.parquet, so insertion
# order has to be preserved. Verified identical to the previous hardcoded lists.
ETYPE_CATS = list(dict.fromkeys(BALLOT_TYPE_MAP.values()))      # G, P
METHOD_CATS = list(dict.fromkeys(BALLOT_METHOD_MAP.values()))   # E, V, A, F, D, M, O
_ETYPE_CODE = {c: i for i, c in enumerate(ETYPE_CATS)}
_METHOD_CODE = {c: i for i, c in enumerate(METHOD_CATS)}


def _push_ballot(prow, yr, et, me, row, year, code) -> bool:
    """Append one ballot if well-formed; return False if it was rejected.

    Shared by both readers. They used to validate differently: from_cache
    counted an unknown code into `bad` and skipped it, while from_csv indexed
    _ETYPE_CODE unguarded and died with a bare KeyError (or IndexError on a
    one-character code).
    """
    try:
        year = int(year)
    except (TypeError, ValueError):
        return False
    if not (1900 <= year <= 2100) or not isinstance(code, str) or len(code) < 2:
        return False
    t, m = code[0].upper(), code[1].upper()
    if t not in _ETYPE_CODE or m not in _METHOD_CODE:
        return False
    prow.append(row)
    yr.append(year)
    et.append(_ETYPE_CODE[t])
    me.append(_METHOD_CODE[m])
    return True

HH_COLS = ["household_uuid", "county", "town", "city", "zip_code",
           "address_number", "street_name", "election_district",
           "congressional_district", "senate_district", "assembly_district",
           "legislative_district", "lon", "lat", "declared_voters"]

ADDR_COLS = ("county", "address_number", "street_name", "zip_code")


def _ballots_frame(prow, yr, et, me) -> pd.DataFrame:
    return pd.DataFrame({
        "person_row": np.asarray(prow, dtype=np.int32),
        "year": np.asarray(yr, dtype=np.int16),
        "etype": pd.Categorical.from_codes(
            np.frombuffer(bytes(et), dtype=np.int8), categories=ETYPE_CATS),
        "method": pd.Categorical.from_codes(
            np.frombuffer(bytes(me), dtype=np.int8), categories=METHOD_CATS),
    })


def _normalise_addresses(hh: pd.DataFrame) -> pd.DataFrame:
    for c in ADDR_COLS:
        hh[c] = hh[c].fillna("").astype(str).str.strip().str.upper()
    return hh


# ------------------------------------------------------------------- cache

def from_cache(county: str | None, city: str | None, cutoff: date):
    if not C.CACHE.exists():
        raise SystemExit(
            f"cache not found at {C.CACHE}\n"
            f"Run: python model/refresh_cache.py   (or use --source csv)")

    print("  households (cache)...")
    hh = pd.read_parquet(C.CACHE / "households.parquet", columns=[
        "id", "county", "town", "city", "zip", "address_num", "street",
        "election_district", "congressional_district", "senate_district",
        "assembly_district", "lon", "lat", "people_count"])
    hh = hh.rename(columns={"id": "household_uuid", "zip": "zip_code",
                            "address_num": "address_number", "street": "street_name",
                            "people_count": "declared_voters"})
    hh["legislative_district"] = pd.NA          # no such column in Supabase

    print("  people (cache)...")
    ppl = pd.read_parquet(C.CACHE / "people.parquet", columns=[
        "id", "household_id", "name", "age", "party", "tier_letter",
        "tier_count", "elections", "donor_key"])
    ppl = ppl.rename(columns={"id": "person_uuid", "household_id": "household_uuid"})

    if county:
        hh = hh[hh["county"].str.upper() == county.upper()]
    if city:
        hh = hh[hh["city"].str.upper() == city.upper()]
    # Always, not just when filtering: refresh_cache.py dumps households and
    # people on separate connections in separate transactions, so the two files
    # can disagree. A person whose household is absent gets NaN geography, which
    # collapses their ed_key AND promotes the district columns to float64 --
    # changing every other voter's ed_key from "NASSAU|12|1" to "NASSAU|12.0|1.0"
    # and breaking the match against splits.parquet for the whole file.
    # Do it here: before the stable sort and the ballot explosion, so
    # ballots.person_row stays aligned.
    known = ppl["household_uuid"].isin(set(hh["household_uuid"]))
    if not known.all():
        lost = ppl.loc[~known, "household_uuid"]
        print(f"    dropping {int((~known).sum()):,} people whose household is "
              f"absent from households.parquet ({lost.nunique():,} household(s), "
              f"e.g. {sorted(lost.unique()[:3].tolist())}) — the two dumps "
              f"disagree; rerun refresh_cache.py to resync")
        ppl = ppl[known]

    # Fixed, reproducible order; ballots index into this positionally.
    ppl = ppl.sort_values("person_uuid", kind="stable").reset_index(drop=True)

    print("  exploding elections...")
    prow, yr = array("i"), array("h")
    et, me = bytearray(), bytearray()
    bad = 0
    for i, raw in enumerate(ppl["elections"].to_numpy()):
        if not isinstance(raw, str) or not raw:
            continue
        try:
            elecs = json.loads(raw)     # cached JSONB arrives as a JSON string
        except ValueError:
            bad += 1
            continue
        for item in elecs or ():
            if not (isinstance(item, (list, tuple)) and len(item) == 2):
                bad += 1
                continue
            y, code = item
            if not _push_ballot(prow, yr, et, me, i, y, code):
                bad += 1
    if bad:
        print(f"    skipped {bad:,} unparseable ballot entries")
    ballots = _ballots_frame(prow, yr, et, me)
    ppl = ppl.drop(columns=["elections"])

    print("  donations (cache)...")
    don = pd.read_parquet(C.CACHE / "donations.parquet", columns=[
        "donor_key", "source", "committee", "amount", "donation_date", "confirmed"])
    n_all = len(don)
    # Dateless records cannot be placed relative to the cutoff, so they go too;
    # keeping them would let post-election giving into an as-of feature.
    don = don[don["confirmed"].fillna(False)
              & don["donation_date"].notna()
              & (pd.to_datetime(don["donation_date"], errors="coerce")
                 < pd.Timestamp(cutoff))]
    don = don.drop(columns=["confirmed"]).reset_index(drop=True)
    don["amount"] = pd.to_numeric(don["amount"], errors="coerce").fillna(0.0)
    print(f"    {len(don):,} of {n_all:,} donations are confirmed and pre-{cutoff}")

    hh = _normalise_addresses(hh)
    print(f"  {len(hh):,} households | {len(ppl):,} people | "
          f"{len(ballots):,} ballots | {len(don):,} donations")
    return hh[HH_COLS], ppl, ballots, don


# --------------------------------------------------------------------- csv

def _geocode(df: pd.DataFrame) -> pd.DataFrame:
    """Interpolate household lat/lon from TIGER; cached, it dominates runtime."""
    cache = C.ARTIFACTS / f"geocode_cache_{len(df)}.parquet"
    if cache.exists():
        cached = pd.read_parquet(cache)
        if len(cached) == len(df):
            print(f"  using geocode cache {cache.name}")
            df["lon"], df["lat"] = cached["lon"].to_numpy(), cached["lat"].to_numpy()
            return df
    print("  building geocoder indexes...")
    geocoders = {c: Geocoder(extract_tiger(c)) for c in df["county"].unique()}
    print(f"  geocoding {len(df):,} households...")
    lons, lats = [], []
    for county, addr, street, zip5 in zip(
            df["county"], df["address_number"], df["street_name"], df["zip_code"]):
        point = geocoders[county].geocode(addr, street, zip5) if addr else None
        lons.append(round(point[0], 5) if point else np.nan)
        lats.append(round(point[1], 5) if point else np.nan)
    df["lon"], df["lat"] = lons, lats
    hits = df["lon"].notna().sum()
    print(f"    geocoded {hits:,}/{len(df):,} ({100 * hits / len(df):.1f}%)")
    df[["lon", "lat"]].to_parquet(cache, index=False)
    return df


def _parse_record_date(raw):
    """Best-effort date from a heterogeneous donation payload field.

    The two FEC producers disagree: fetch_fec_bulk.py emits 'YYYY-MM-DD' (what
    the committed dist/*.b64 payloads hold) while fetch_fec.py stores the API's
    raw contribution_receipt_date, an ISO 8601 timestamp. The old parser split
    on '-' and int()ed the parts, so '2016-10-31T00:00:00' raised ValueError
    and was counted as "dateless" — refreshing data/fec_cache.json through the
    API path would have zeroed every FEC donation feature without an error.

    None means "no usable date"; the caller drops those, because a record that
    cannot be placed relative to the cutoff would leak post-election giving
    into an as-of feature. A value that is neither a date nor a string raises,
    because that is a payload schema change and not a per-record problem.
    """
    if raw is None or raw == "":
        return None
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    if not isinstance(raw, str):
        raise TypeError(
            f"unsupported donation date {raw!r} ({type(raw).__name__}) — the "
            f"payload schema has changed")
    try:
        return date.fromisoformat(raw.strip()[:10])   # date or timestamp head
    except ValueError:
        return None


def _load_donation_payloads(cutoff: date) -> pd.DataFrame:
    """Flatten the dist/*.b64 donor tables into the common donations frame."""
    def read(cache_path: Path, b64_path: Path, label: str) -> dict:
        if cache_path.exists():
            raw = json.loads(cache_path.read_text())
            tbl = {k: {"c": v["confirmed"]} for k, v in raw.items() if v.get("confirmed")}
            print(f"  {label}: {len(tbl):,} confirmed donors (from {cache_path.name})")
            return tbl
        payload = json.loads(gzip.decompress(base64.b64decode(b64_path.read_text())))
        tbl = payload.get("fec_donations", payload)
        print(f"  {label}: {len(tbl):,} confirmed donors (from {b64_path.name})")
        return tbl

    rows = []
    for source, tbl in (("fec", read(C.FEC_CACHE, C.COUNTY_B64, "FEC")),
                        ("nyboe", read(C.NYBOE_CACHE, C.NYBOE_B64, "NYBOE"))):
        kept = post = absent = unparsed = 0
        for key, val in tbl.items():
            for r in (val.get("c") or []):
                raw = r.get("date")
                rec_date = _parse_record_date(raw)
                if rec_date is None:
                    absent += (raw is None or raw == "")
                    unparsed += not (raw is None or raw == "")
                    continue
                if rec_date >= cutoff:
                    post += 1
                    continue
                rows.append((key, source, r.get("committee") or "",
                             r.get("amount") or 0.0, rec_date))
                kept += 1
        seen = kept + post + absent + unparsed
        print(f"  {source}: kept {kept:,} of {seen:,} records before {cutoff} "
              f"({post:,} post-cutoff, {absent:,} dateless, {unparsed:,} unparseable)")
        # A format change looks exactly like "this source has no dated records".
        # Zeroed donation features for 1.9M voters is not something to infer from
        # a log line nobody reads.
        if seen and unparsed > max(10, 0.01 * seen):
            raise ValueError(
                f"{source}: {unparsed:,} of {seen:,} donation dates "
                f"({100 * unparsed / seen:.1f}%) could not be parsed — the payload's "
                f"date format has changed. Fix _parse_record_date rather than "
                f"shipping a zeroed donation feature set.")
    return pd.DataFrame(rows, columns=["donor_key", "source", "committee",
                                       "amount", "donation_date"])


def from_csv(county: str | None, city: str | None, cutoff: date):
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

    def clean_str(s: pd.Series) -> pd.Series:
        out = s.astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
        return out.where(~out.str.lower().isin(["nan", "null", "none", "<na>", ""]), "")
    df["address_number"] = clean_str(df["address_number"])
    df["zip_code"] = clean_str(df["zip_code"])
    print(f"  {len(df):,} household rows")
    df = _geocode(df)

    hh = df.rename(columns={}).copy()
    # The CSV has no stable household key; the row index is one, and it is
    # what household_row would have been anyway.
    hh["household_uuid"] = np.arange(len(hh), dtype=np.int64).astype(str)
    # The export's own per-household count, kept so etl.report() can check the
    # parse against it rather than against a number derived from the parse.
    hh["declared_voters"] = pd.to_numeric(df["voters_at_address"], errors="coerce")

    print("  exploding households into persons...")
    prow, yr = array("i"), array("h")
    et, me = bytearray(), bytearray()
    people_rows, skipped, bad = [], 0, 0
    for hh_row, rec in enumerate(df.itertuples(index=False)):
        people = parse_household(rec.household_detail)
        if not people:
            skipped += 1
            continue
        city_u = str(rec.city).upper().strip()
        zip5 = str(rec.zip_code).strip()
        for name, age, party, tier, elections in people:
            letter, digits = tier[0], tier[1:]
            key = f"{name}|{city_u}|{zip5}"
            people_rows.append((
                # keep a stable synthetic id so downstream code that expects
                # person_uuid does not have to special-case this source
                hashlib.blake2b(
                    f"{key}|{rec.county}|{rec.address_number} {rec.street_name}".encode(),
                    digest_size=8).hexdigest(),
                str(hh_row), name, age, party, letter,
                int(digits) if digits.isdigit() else 0, key))
            i = len(people_rows) - 1
            for e_year, e_code in elections:
                if not _push_ballot(prow, yr, et, me, i, e_year, e_code):
                    bad += 1
    if skipped:
        print(f"  {skipped:,} household rows had no parseable people")
    if bad:
        print(f"  skipped {bad:,} unparseable ballot entries")

    ppl = pd.DataFrame(people_rows, columns=[
        "person_uuid", "household_uuid", "name", "age", "party",
        "tier_letter", "tier_count", "donor_key"])
    ballots = _ballots_frame(prow, yr, et, me)
    df.drop(columns=["household_detail"], inplace=True)

    print(f"  loading donation payloads (as-of cutoff {cutoff})...")
    don = _load_donation_payloads(cutoff)

    hh = _normalise_addresses(hh)
    print(f"  {len(hh):,} households | {len(ppl):,} people | "
          f"{len(ballots):,} ballots | {len(don):,} donations")
    return hh[HH_COLS], ppl, ballots, don


READERS = {"cache": from_cache, "csv": from_csv}
