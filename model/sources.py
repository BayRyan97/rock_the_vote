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
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

import config as C

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "build"))
from build import Geocoder, extract_tiger, parse_household  # noqa: E402

# Election-history vocabulary: the single chars build.parse_household emits.
ETYPE_CATS = ["G", "P"]                              # general, primary
METHOD_CATS = ["E", "V", "A", "F", "D", "M", "O"]    # poll site, early, absentee,
                                                     # federal, affidavit, mail, other
_ETYPE_CODE = {c: i for i, c in enumerate(ETYPE_CATS)}
_METHOD_CODE = {c: i for i, c in enumerate(METHOD_CATS)}

HH_COLS = ["household_uuid", "county", "town", "city", "zip_code",
           "address_number", "street_name", "election_district",
           "congressional_district", "senate_district", "assembly_district",
           "legislative_district", "lon", "lat"]

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
        "assembly_district", "lon", "lat"])
    hh = hh.rename(columns={"id": "household_uuid", "zip": "zip_code",
                            "address_num": "address_number", "street": "street_name"})
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
    if county or city:
        ppl = ppl[ppl["household_uuid"].isin(set(hh["household_uuid"]))]

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
            try:
                y = int(y)
            except (TypeError, ValueError):
                bad += 1
                continue
            if not (1900 <= y <= 2100) or not isinstance(code, str) or len(code) < 2:
                bad += 1
                continue
            t, m = code[0].upper(), code[1].upper()
            if t not in _ETYPE_CODE or m not in _METHOD_CODE:
                bad += 1
                continue
            prow.append(i)
            yr.append(y)
            et.append(_ETYPE_CODE[t])
            me.append(_METHOD_CODE[m])
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

    rows, dropped_post, dropped_dateless = [], 0, 0
    for source, tbl in (("fec", read(C.FEC_CACHE, C.COUNTY_B64, "FEC")),
                        ("nyboe", read(C.NYBOE_CACHE, C.NYBOE_B64, "NYBOE"))):
        for key, val in tbl.items():
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
                rows.append((key, source, r.get("committee") or "",
                             r.get("amount") or 0.0, rec_date))
    print(f"  kept {len(rows):,} donation records before {cutoff} "
          f"(dropped {dropped_post:,} post-cutoff + {dropped_dateless:,} dateless)")
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

    print("  exploding households into persons...")
    prow, yr = array("i"), array("h")
    et, me = bytearray(), bytearray()
    people_rows, skipped = [], 0
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
                if not 1900 <= e_year <= 2100:
                    continue
                prow.append(i)
                yr.append(e_year)
                et.append(_ETYPE_CODE[e_code[0]])
                me.append(_METHOD_CODE[e_code[1]])
    if skipped:
        print(f"  {skipped:,} household rows had no parseable people")

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
