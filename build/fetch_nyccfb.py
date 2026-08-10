#!/usr/bin/env python3
"""
fetch_nyccfb.py — matches voters against NYC Campaign Finance Board contributions.

NY State BOE only covers state-level races (Governor, State Senate/Assembly,
AG, Comptroller). NYC races — Mayor, Public Advocate, Comptroller, Borough
President, City Council — are regulated separately by the NYC CFB, so a Long
Island donor giving to a NYC candidate is invisible to fetch_nyboe.py. This
script pulls NYC's "Campaign Contributions" dataset from NYC Open Data
(data.cityofnewyork.us, dataset rjkp-yttg — Socrata, same API family as NY
BOE's data.ny.gov), matches against voters in Supabase (via voter_source.py
— no local voter CSV needed) by name + city + zip, and writes to
data/nyccfb_cache.json.

Unlike NY BOE, this dataset has genuine per-donor employer/occupation fields
(empname/occupation), so nyccfb-sourced cache entries carry them from the
first run — no separate backfill needed the way FEC's did.

The dataset's "name" field is a single "LAST, FIRST" string (not split
first/last columns like NY BOE), so name parsing here reuses the same
sorted-token-set approach fetch_fec_bulk.py uses for FEC's bulk file names.

Output format mirrors fec_cache.json/nyboe_cache.json so build.py and
migrate_donations_psycopg2.py need only a source-list entry, no shape changes.

Usage:
    python build/fetch_nyccfb.py                  # all parties, BLK+DEM dropoff voters
    python build/fetch_nyccfb.py --all-parties     # all registered voters
    python build/fetch_nyccfb.py --dry-run         # don't write cache
    python build/fetch_nyccfb.py --no-download     # use existing nyccfb_contributions.csv
"""
import argparse
import csv
import json
import os
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests

import voter_source

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CACHE_FILE = DATA / "nyccfb_cache.json"
LOCK_FILE  = DATA / ".nyccfb_fetch.lock"
NY_CSV     = DATA / "nyccfb_contributions.csv"

POSSIBLE_CAP   = 10

# NYC Open Data Socrata API (data.cityofnewyork.us) — "Campaign Contributions"
# dataset, 1900-present. No auth required, same as data.ny.gov; if paging gets
# throttled in practice, register a free app token at
# data.cityofnewyork.us and add it as an X-App-Token header.
SOCRATA_BASE = "https://data.cityofnewyork.us/resource/rjkp-yttg.json"
PAGE_SIZE    = 50_000

# Nassau/Suffolk zip range
ZIP_MIN = "11000"
ZIP_MAX = "11999"


# ---------- helpers -----------------------------------------------------------

def now_iso():
    return datetime.now(timezone.utc).isoformat()


def normalize_name(raw_name):
    """'RUBENSTEIN, BARRY M' -> 'BARRY|M|RUBENSTEIN' (sorted alpha tokens, pipe-joined)"""
    return "|".join(sorted(re.findall(r"[A-Z]+", raw_name.upper())))


def get_last_name(raw_name):
    """'RUBENSTEIN, BARRY M' -> 'RUBENSTEIN' (index lookup key)"""
    return raw_name.split(",", 1)[0].strip().upper()


# ---------- lock file ---------------------------------------------------------

def acquire_lock():
    if LOCK_FILE.exists():
        age_min = (time.time() - LOCK_FILE.stat().st_mtime) / 60
        sys.exit(
            f"Another fec fetch run appears to be in progress "
            f"(lock is {age_min:.0f} min old). Delete {LOCK_FILE} and retry."
        )
    LOCK_FILE.write_text(str(os.getpid()))


def release_lock():
    LOCK_FILE.unlink(missing_ok=True)


# ---------- cache -------------------------------------------------------------

def load_cache():
    if CACHE_FILE.exists():
        return json.loads(CACHE_FILE.read_text())
    return {}


def save_cache(cache):
    tmp = CACHE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cache))
    tmp.replace(CACHE_FILE)


# ---------- download ----------------------------------------------------------

def download_nyccfb(dest_csv, refilter=False):
    """Page through Socrata API, write Nassau+Suffolk contribution rows to CSV."""
    if dest_csv.exists() and not refilter:
        size_mb = dest_csv.stat().st_size // (1024 * 1024)
        print(f"  Using existing {dest_csv.name} ({size_mb} MB) — pass --refilter to re-download")
        with open(dest_csv) as f:
            return sum(1 for _ in f) - 1  # subtract header

    print("  Fetching NYC CFB contributions for Nassau+Suffolk zips from data.cityofnewyork.us...")
    tmp_csv = dest_csv.with_suffix(".csv.tmp")
    row_count = 0
    offset = 0

    with open(tmp_csv, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["name", "city", "zip5", "date", "amount", "committee", "employer", "occupation"])

        while True:
            params = {
                "$where": f"zip between '{ZIP_MIN}' and '{ZIP_MAX}'",
                "$select": "name,city,zip,date,amnt,recipname,empname,occupation",
                "$limit": PAGE_SIZE,
                "$offset": offset,
                "$order": ":id",
            }
            resp = requests.get(SOCRATA_BASE, params=params, timeout=60)
            resp.raise_for_status()
            rows = resp.json()
            if not rows:
                break

            for r in rows:
                name = (r.get("name") or "").strip().upper()
                if "," not in name:
                    continue  # organizations/LLCs — no "LAST, FIRST" to match against
                city = (r.get("city") or "").strip().upper()
                zip5 = (r.get("zip")  or "").strip()[:5]
                date = (r.get("date") or "")[:10]  # ISO date
                amt  = r.get("amnt", "0") or "0"
                committee  = (r.get("recipname") or "").strip()
                employer   = (r.get("empname")   or "").strip()
                occupation = (r.get("occupation") or "").strip()
                try:
                    amount = float(amt)
                except ValueError:
                    amount = 0.0
                writer.writerow([name, city, zip5, date, amount, committee, employer, occupation])
                row_count += 1

            offset += PAGE_SIZE
            print(f"    {row_count:,} rows fetched...")

            if len(rows) < PAGE_SIZE:
                break

    tmp_csv.rename(dest_csv)
    print(f"  {row_count:,} rows written to {dest_csv.name}")
    return row_count


# ---------- index builder -----------------------------------------------------

def build_name_index(csv_path):
    """Build {last_name: [(name_norm, city, zip5, date, amount, committee, employer, occupation)]} index."""
    index = defaultdict(list)
    total = 0
    print(f"  Indexing {csv_path.name}...")
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = row["name"]
            if not name:
                continue
            norm = normalize_name(name)
            last = get_last_name(name)
            try:
                amount = float(row["amount"])
            except ValueError:
                amount = 0.0
            index[last].append((
                norm, row["city"], row["zip5"], row["date"], amount,
                row["committee"] or None, row["employer"] or None, row["occupation"] or None,
            ))
            total += 1
    print(f"  Index: {len(index):,} unique last names, {total:,} total records")
    return index


# ---------- matching ----------------------------------------------------------

def classify(index, voter_name, voter_city, voter_zip5):
    last = voter_name.upper().split()[-1]
    v_tokens = frozenset(re.findall(r"[A-Z]+", voter_name.upper()))
    confirmed, possible = [], []
    for (name_norm, city, zip5, date, amount, committee, employer, occupation) in index.get(last, []):
        fec_tokens = frozenset(name_norm.split("|"))
        if not v_tokens.issubset(fec_tokens):
            continue
        record = {
            "date": date, "amount": amount, "committee": committee,
            "employer": employer, "occupation": occupation,
        }
        if city == voter_city.strip().upper() and zip5 == voter_zip5:
            confirmed.append(record)
        else:
            possible.append(record)
    return confirmed, possible[:POSSIBLE_CAP]




# ---------- main --------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-download", action="store_true",
                    help="use existing nyccfb_contributions.csv, skip API fetch")
    ap.add_argument("--refilter", action="store_true",
                    help="re-download even if CSV already exists")
    ap.add_argument("--dry-run", action="store_true",
                    help="process but don't write cache")
    ap.add_argument("--limit", type=int, default=None,
                    help="process only first N voters (testing)")
    ap.add_argument("--all-parties", action="store_true",
                    help="match every registered voter regardless of party")
    ap.add_argument("--include-rep", action="store_true",
                    help="also match Republican-registered voters")
    ap.add_argument("--county", type=str, default=None,
                    help="filter to NASSAU or SUFFOLK only")
    args = ap.parse_args()

    acquire_lock()
    t_start = time.monotonic()
    try:
        # Phase 1: Download
        print("=== Phase 1: Fetch NYC CFB contributions ===")
        if not args.no_download:
            download_nyccfb(NY_CSV, refilter=args.refilter)
        elif not NY_CSV.exists():
            sys.exit(f"{NY_CSV} not found — run without --no-download first")
        else:
            size_mb = NY_CSV.stat().st_size // (1024 * 1024)
            print(f"  Using existing {NY_CSV.name} ({size_mb} MB)")

        # Phase 2: Index
        print("\n=== Phase 2: Build name index ===")
        index = build_name_index(NY_CSV)

        # Phase 3: Match
        print("\n=== Phase 3: Match voters ===")
        cache = load_cache()
        print(f"Existing NYC CFB cache: {len(cache):,} entries")

        total = confirmed_new = possible_new = 0
        for key, name, city, zip5 in voter_source.iter_voters(args.county, args.include_rep, args.all_parties):
            if args.limit and total >= args.limit:
                break
            total += 1

            confirmed, possible = classify(index, name, city, zip5)
            if confirmed or possible:
                entry = {
                    "confirmed": confirmed,
                    "possible":  possible,
                    "checked_at": now_iso(),
                    "source": "nyccfb",
                }
                if not args.dry_run:
                    cache[key] = entry
                if confirmed:
                    confirmed_new += 1
                elif possible:
                    possible_new += 1

            if total % 100_000 == 0:
                print(f"  {total:,} processed | {confirmed_new:,} confirmed | {possible_new:,} possible")

        # Phase 4: Save
        if not args.dry_run:
            print("\n=== Phase 4: Save ===")
            save_cache(cache)
            print(f"  Saved {len(cache):,} entries to {CACHE_FILE.name}")

        elapsed = time.monotonic() - t_start
        print(f"\n=== Done in {elapsed/60:.1f} min ===")
        print(f"  Voters processed: {total:,}")
        print(f"  Confirmed (NYC-level): {confirmed_new:,}")
        print(f"  Possible-only: {possible_new:,}")
        if args.dry_run:
            print("  [DRY RUN — cache not written]")

    finally:
        release_lock()


if __name__ == "__main__":
    main()
