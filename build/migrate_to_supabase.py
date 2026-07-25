"""
migrate_to_supabase.py — one-time data migration from voter CSVs + caches into Supabase.

Wraps the existing geocoding/scoring logic from build.py rather than reimplementing it.
Run from the repo root after setting environment variables:

    export SUPABASE_URL="https://xxxx.supabase.co"
    export SUPABASE_SERVICE_ROLE_KEY="eyJ..."
    python build/migrate_to_supabase.py [--households] [--donations] [--ev] [--limit N]

Flags:
  --households   Migrate voter households + people (slow: 2-4h first run due to geocoding)
  --donations    Migrate confirmed FEC + NYBOE donations from data/fec_cache.json etc.
  --ev           Migrate ZIP-level EV scores from data/ev_zip_scores.json
  --limit N      Only process first N households (for testing)
  --all          Run all three stages
"""

import argparse
import hashlib
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Optional

import psycopg2
from psycopg2.extras import execute_values, Json

# Add build/ to path so we can import from build.py
BUILD = Path(__file__).parent
ROOT = BUILD.parent
sys.path.insert(0, str(BUILD))

from build import (
    Geocoder,
    extract_tiger,
    load_voter_file,
    parse_household,
    score_household,
    VOTER_SOURCES,
)
from supabase import create_client, Client

DATA = ROOT / "data"
BATCH_SIZE = 500   # larger batches — psycopg2 has no HTTP/2 stream limit
DONATION_BATCH = 5000

DSN = "postgresql://postgres.sqpjghpvgmahbodlkffl:ugSfCdhhtDEXP65k@aws-1-us-west-2.pooler.supabase.com:5432/postgres"


def _household_uuid(county: str, address_num: str, street: str, zip5: str) -> str:
    """Deterministic UUID so the same address always gets the same ID,
    even across retries or re-runs."""
    key = f"{county}|{address_num}|{street}|{zip5}".encode()
    h = hashlib.md5(key).digest()
    return str(uuid.UUID(bytes=h))


def make_client() -> Client:
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        sys.exit("Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY environment variables.")
    return create_client(url, key)


# ----------------------------------------------------------------- households

def migrate_households(supabase: Client, limit: Optional[int] = None):
    import pandas as pd

    print("Loading voter files...")
    frames = []
    for path in VOTER_SOURCES:
        if path.exists():
            frames.append(load_voter_file(path))
        else:
            print(f"  WARNING: {path} not found, skipping")
    if not frames:
        sys.exit("No voter source files found.")

    df = pd.concat(frames, ignore_index=True)
    if limit:
        df = df.head(limit)
    print(f"  {len(df):,} total rows to migrate")

    conn = psycopg2.connect(DSN)
    conn.autocommit = False
    cur = conn.cursor()

    geocoders: dict[str, Geocoder] = {}
    total, geocoded = 0, 0

    households_batch: list[tuple] = []
    people_batch: list[tuple] = []

    for _, row in df.iterrows():
        county = str(row.get("county", "")).strip().upper()
        if county not in ("NASSAU", "SUFFOLK"):
            continue

        people = parse_household(row.get("household_detail", ""))
        if not people:
            continue

        zip5 = str(row.get("zip_code", "")).strip().replace(".0", "").zfill(5)
        address_num = str(row.get("address_number", "")).strip()
        street = str(row.get("street_name", "")).strip().upper()
        city = str(row.get("city", "")).strip().upper()

        hh_id = _household_uuid(county, address_num, street, zip5)

        if county not in geocoders:
            print(f"  building {county} geocoder...")
            shapefile_base = extract_tiger(county)
            geocoders[county] = Geocoder(shapefile_base)

        result = geocoders[county].geocode(address_num, street, zip5)
        lon, lat = (round(result[0], 5), round(result[1], 5)) if result else (None, None)
        if result:
            geocoded += 1

        wake_ups, unaffiliated, dropoff, total_score = score_household(people)

        town = str(row.get("town", "")).strip().upper() if row.get("town") else None
        households_batch.append((
            hh_id,
            county,
            str(row.get("address_number", "")).strip(),
            str(row.get("street_name", "")).strip().upper(),
            city,
            zip5,
            town,
            _safe_int(row.get("election_district")),
            _safe_int(row.get("assembly_district")),
            _safe_int(row.get("senate_district")),
            _safe_int(row.get("congressional_district")),
            lon,
            lat,
            total_score,
            wake_ups,
            unaffiliated,
            dropoff,
        ))

        for p in people:
            name, age, party, tier, elections = p
            letter = tier[0] if tier else "I"
            digits = tier[1:] if len(tier) > 1 else "0"
            count = int(digits) if digits.isdigit() else 0
            people_batch.append((
                hh_id,
                name,
                int(age) if age else None,
                party if party in ("DEM","REP","BLK","WOR","CON","IND","OTH") else "OTH",
                letter if letter in ("X","F","L","I") else "I",
                count,
                Json(elections) if elections else None,
                city,
                zip5,
            ))

        total += 1
        if len(households_batch) >= BATCH_SIZE:
            _flush_psycopg2(cur, households_batch, people_batch)
            conn.commit()
            households_batch, people_batch = [], []
            if total % 5000 == 0:
                pct = 100 * geocoded / total
                print(f"  {total:,} households migrated ({pct:.0f}% geocoded)...")

    if households_batch:
        _flush_psycopg2(cur, households_batch, people_batch)
        conn.commit()

    conn.close()
    print(f"Done: {total:,} households, {geocoded:,} geocoded ({100*geocoded/total:.1f}%)")


def _flush_psycopg2(cur, households: list[tuple], people: list[tuple]):
    execute_values(
        cur,
        """
        INSERT INTO households
          (id, county, address_num, street, city, zip, town,
           election_district, assembly_district, senate_district, congressional_district,
           lon, lat, score_total, score_wake_ups, score_unaffiliated, score_dropoff)
        VALUES %s
        ON CONFLICT (county, address_num, street, zip) DO NOTHING
        """,
        households,
    )
    if people:
        execute_values(
            cur,
            """
            INSERT INTO people
              (household_id, name, age, party, tier_letter, tier_count, elections, city, zip)
            VALUES %s
            ON CONFLICT (household_id, name) DO NOTHING
            """,
            people,
        )


def _safe_int(val) -> Optional[int]:
    try:
        return int(float(val))
    except (TypeError, ValueError):
        return None


# ----------------------------------------------------------------- donations

def migrate_donations(supabase: Client):
    sources = [
        (DATA / "fec_cache.json", "fec"),
        (DATA / "nyboe_cache.json", "nyboe"),
    ]
    conn = psycopg2.connect(DSN)
    conn.autocommit = False
    cur = conn.cursor()

    for cache_path, source in sources:
        if not cache_path.exists():
            print(f"  {cache_path.name} not found, skipping")
            continue
        print(f"  loading {cache_path.name}...")
        cache = json.loads(cache_path.read_text())
        batch: list[tuple] = []
        inserted = 0
        for donor_key, entry in cache.items():
            for item in entry.get("confirmed", []):
                batch.append((
                    donor_key.upper(),
                    source,
                    _safe_str(item.get("contribution_receipt_date") or item.get("date")),
                    _safe_float(item.get("contribution_receipt_amount") or item.get("amount")),
                    _safe_str(item.get("committee_name") or item.get("filer_name")),
                    True,
                ))
                if len(batch) >= DONATION_BATCH:
                    execute_values(
                        cur,
                        "INSERT INTO donations (donor_key, source, donation_date, amount, committee, confirmed) VALUES %s",
                        batch,
                        template="(%s, %s, %s::date, %s, %s, %s)",
                    )
                    conn.commit()
                    inserted += len(batch)
                    batch = []
                    if inserted % 50000 == 0:
                        print(f"  {source}: {inserted:,} inserted...")
        if batch:
            execute_values(
                cur,
                "INSERT INTO donations (donor_key, source, donation_date, amount, committee, confirmed) VALUES %s",
                batch,
                template="(%s, %s, %s::date, %s, %s, %s)",
            )
            conn.commit()
            inserted += len(batch)
        print(f"  {source}: {inserted:,} confirmed donations migrated")

    conn.close()


def _safe_str(val) -> Optional[str]:
    """Return None for empty/whitespace strings."""
    if not val or not str(val).strip():
        return None
    return str(val).strip()


def _safe_float(val) -> Optional[float]:
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


# ----------------------------------------------------------------- EV scores

def migrate_ev(supabase: Client):
    scores_path = DATA / "ev_zip_scores.json"
    counts_path = DATA / "ev_zip_counts.json"
    if not scores_path.exists():
        print(f"  {scores_path.name} not found — run build/fetch_ev.py first")
        return
    scores = json.loads(scores_path.read_text())
    counts = json.loads(counts_path.read_text()) if counts_path.exists() else {}
    rows = [
        {"zip": z, "score": int(s), "count": int(counts.get(z, 0))}
        for z, s in scores.items()
    ]
    for i in range(0, len(rows), BATCH_SIZE):
        supabase.table("ev_scores").upsert(
            rows[i:i + BATCH_SIZE], on_conflict="zip"
        ).execute()
    print(f"  {len(rows):,} ZIP EV scores migrated")


# ----------------------------------------------------------------------- main

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--households", action="store_true")
    parser.add_argument("--donations", action="store_true")
    parser.add_argument("--ev", action="store_true")
    parser.add_argument("--all", dest="all_stages", action="store_true")
    parser.add_argument("--limit", type=int, default=None,
                        help="Only process first N households (for testing)")
    args = parser.parse_args()

    if not (args.households or args.donations or args.ev or args.all_stages):
        parser.print_help()
        sys.exit(1)

    supabase = make_client()

    if args.all_stages or args.ev:
        print("--- EV scores ---")
        migrate_ev(supabase)

    if args.all_stages or args.donations:
        print("--- Donations ---")
        migrate_donations(supabase)

    if args.all_stages or args.households:
        print("--- Households + People ---")
        migrate_households(supabase, limit=args.limit)


if __name__ == "__main__":
    main()
