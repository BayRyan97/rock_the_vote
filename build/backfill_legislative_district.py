#!/usr/bin/env python3
"""backfill_legislative_district.py — one-time backfill of
households.legislative_district from the raw Nassau/Suffolk voter export CSVs.

households.legislative_district (added by supabase/migrations/
032_households_legislative_district.sql) was never populated:
build/migrate_to_supabase.py's migrate_households() never read the column out
of the source rows, even though every export has always carried it —
NY county legislature district (Nassau/Suffolk County Legislature, local
government, distinct from the assembly_district/senate_district columns,
which are NYS Assembly/Senate). This script re-derives each row's household
id the same deterministic way migrate_to_supabase.py does (_household_uuid)
and UPDATEs the matching row. No re-geocoding needed — lon/lat/turf_id are
untouched.

Reads the SAME files build/build.py's VOTER_SOURCES points migrate_households()
at (Nassau_Unrolled.csv / Suffolk_Unrolled.csv — one row per registered voter,
NOT the smaller pre-aggregated Nassau.csv/Suffolk.csv), so household ids come
out identical to what's already in the DB and nothing drifts between what was
originally migrated and what this backfills.

A household with no matching row in the source CSVs is left NULL — same
convention as an un-geocoded or unmatched AD/city/town today. A CSV row with
no matching household in Supabase (never migrated, e.g. failed geocoding)
simply updates zero rows; the read-vs-updated counts below make a bad match
rate visible rather than silently accepted.

Idempotent: the UPDATE's `IS DISTINCT FROM` guard makes re-running a no-op for
anything already correct, so it's safe to run again after an interruption or
a refreshed export.

Usage:
    python build/backfill_legislative_district.py --dry-run   # sanity check first
    python build/backfill_legislative_district.py             # then for real
    python build/backfill_legislative_district.py --limit 5000 --dry-run  # smoke test

Environment:
    DATABASE_URL  — Postgres DSN (loaded from .env.local or .env)
"""
import argparse
import os
import sys
from pathlib import Path

import pandas as pd
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
BUILD = Path(__file__).resolve().parent
sys.path.insert(0, str(BUILD))
from migrate_to_supabase import _household_uuid, _safe_int  # noqa: E402

load_dotenv(ROOT / ".env.local")
load_dotenv(ROOT / ".env")

DATABASE_URL = os.environ["DATABASE_URL"]

# Same files build/build.py's VOTER_SOURCES feeds migrate_households() — not
# the smaller pre-aggregated data/Nassau.csv / data/Suffolk.csv, which share
# an identical column schema but were never what was actually loaded.
SOURCES = [ROOT / "data" / "Nassau_Unrolled.csv", ROOT / "data" / "Suffolk_Unrolled.csv"]

CHUNK_SIZE = 20_000
BATCH_SIZE = 2_000

UPDATE_SQL = """
UPDATE households AS h
SET legislative_district = v.leg_district
FROM (VALUES %s) AS v(hh_id, leg_district)
WHERE h.id = v.hh_id::uuid
  AND h.legislative_district IS DISTINCT FROM v.leg_district
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None,
                        help="Only process first N rows per source file (for testing)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print("Pass 1: reading household -> legislative_district from the source CSVs ...")
    by_hh: dict[str, int] = {}
    read, no_leg, bad_county = 0, 0, 0

    for path in SOURCES:
        if not path.exists():
            print(f"  WARNING: {path} not found, skipping")
            continue
        print(f"  {path.name} ...")
        file_rows = 0
        stop = False
        for chunk in pd.read_csv(path, dtype=str, chunksize=CHUNK_SIZE,
                                  keep_default_na=False, index_col=False):
            for _, row in chunk.iterrows():
                if args.limit and file_rows >= args.limit:
                    stop = True
                    break
                file_rows += 1
                read += 1

                county = str(row.get("county", "")).strip().upper()
                if county not in ("NASSAU", "SUFFOLK"):
                    bad_county += 1
                    continue
                leg = _safe_int(row.get("legislative_district"))
                if leg is None:
                    no_leg += 1
                    continue

                # Same normalisation migrate_to_supabase.py's migrate_households()
                # uses to build hh_id — must match exactly, or this backfills the
                # wrong row (or no row at all).
                zip5 = str(row.get("zip_code", "")).strip().replace(".0", "").zfill(5)
                address_num = str(row.get("address_number", "")).strip()
                street = str(row.get("street_name", "")).strip().upper()
                hh_id = _household_uuid(county, address_num, street, zip5)

                # First occurrence wins — every duplicate row for the same
                # household in an "unrolled" (one-row-per-voter) export shares
                # the same address fields, so they'd agree anyway.
                by_hh.setdefault(hh_id, leg)

                if read % 200_000 == 0:
                    print(f"    {read:,} rows read, {len(by_hh):,} unique households so far ...")
            if stop:
                break

    print(f"  Done — {read:,} rows read, {len(by_hh):,} unique households with a "
          f"legislative_district ({no_leg:,} rows had none, {bad_county:,} rows had "
          f"neither NASSAU nor SUFFOLK as county)")

    if args.dry_run:
        print("\n(dry-run: skipping DB write)")
        return

    print("\nPass 2: writing legislative_district to households ...")
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    items = list(by_hh.items())
    updated = 0
    for i in range(0, len(items), BATCH_SIZE):
        batch = items[i:i + BATCH_SIZE]
        # page_size >= len(batch) forces exactly one statement per batch, so
        # cur.rowcount below reflects the whole batch rather than just its
        # last internal page (execute_values defaults to page_size=100).
        psycopg2.extras.execute_values(cur, UPDATE_SQL, batch, page_size=len(batch))
        updated += cur.rowcount
        conn.commit()
        if (i // BATCH_SIZE) % 20 == 0:
            print(f"  {i + len(batch):,} / {len(items):,} households processed ...")
    cur.close()
    conn.close()
    print(f"  Done — {updated:,} households updated (of {len(items):,} candidates; "
          f"the gap is households already correct, or with no match in Supabase)")


if __name__ == "__main__":
    main()
