#!/usr/bin/env python3
"""
fetch_nyboe.py — matches voters against NY State Board of Elections campaign finance data.

The FEC only covers federal races. NY BOE covers state-level races: Governor,
State Senate, State Assembly, AG, Comptroller, etc. This script pulls Schedule A
(contributions) from the NY BOE dataset on data.ny.gov, matches against the voter
file by name + city + zip, and writes to data/nyboe_cache.json.

The data.ny.gov API returns all NY BOE contributions since 1999 without any
auth, rate limiting, or Cloudflare protection. We page through 50k rows at a
time, filtering to Nassau+Suffolk zip codes (110xx-119xx).

Output format mirrors fec_cache.json so build.py can combine both sources.

Usage:
    python build/fetch_nyboe.py                  # all parties, BLK+DEM dropoff voters
    python build/fetch_nyboe.py --all-parties     # all registered voters
    python build/fetch_nyboe.py --dry-run         # don't write cache
    python build/fetch_nyboe.py --no-download     # use existing nyboe_ny.csv
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

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
VOTER_SOURCES = [DATA / "Nassau.csv", DATA / "Suffolk.csv"]
CACHE_FILE = DATA / "nyboe_cache.json"
LOCK_FILE  = DATA / ".fec_fetch.lock"
NY_CSV     = DATA / "nyboe_contributions.csv"

PERSON_PATTERN = re.compile(r"^(.*) \((\d+), ([A-Z]+), ([A-Z0-9]+)\)$")
DROPOFF_TIERS  = {"I0", "F1", "L1"}
POSSIBLE_CAP   = 10

# data.ny.gov Socrata API — no auth required
SOCRATA_BASE = "https://data.ny.gov/resource/e9ss-239a.json"
PAGE_SIZE    = 50_000

# Nassau/Suffolk zip range
ZIP_MIN = "11000"
ZIP_MAX = "11999"


# ---------- helpers -----------------------------------------------------------

def now_iso():
    return datetime.now(timezone.utc).isoformat()


def parse_household(detail):
    if not isinstance(detail, str) or not detail.strip():
        return []
    people = []
    for entry in detail.split(" | "):
        m = PERSON_PATTERN.match(entry.strip())
        if m:
            people.append((m.group(1), m.group(3), m.group(4), int(m.group(2))))
    return people


def priority_names(people, include_rep=False, all_parties=False):
    if all_parties:
        return [(name, party, tier, age) for name, party, tier, age in people]
    out = []
    for name, party, tier, age in people:
        if party == "BLK" or (party == "DEM" and tier in DROPOFF_TIERS):
            out.append((name, party, tier, age))
        elif include_rep and party == "REP":
            out.append((name, party, tier, age))
    return out


def normalize_name(raw_first, raw_last):
    """Return sorted alpha token set as pipe-joined string for matching."""
    tokens = re.findall(r"[A-Z]+", f"{raw_first} {raw_last}".upper())
    return "|".join(sorted(tokens))


def get_last_name(raw_last):
    return raw_last.strip().upper()


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

def download_nyboe(dest_csv, refilter=False):
    """Page through Socrata API, write Nassau+Suffolk Schedule A rows to CSV."""
    if dest_csv.exists() and not refilter:
        size_mb = dest_csv.stat().st_size // (1024 * 1024)
        print(f"  Using existing {dest_csv.name} ({size_mb} MB) — pass --refilter to re-download")
        with open(dest_csv) as f:
            return sum(1 for _ in f) - 1  # subtract header

    print("  Fetching NY BOE contributions for Nassau+Suffolk zips from data.ny.gov...")
    tmp_csv = dest_csv.with_suffix(".csv.tmp")
    row_count = 0
    offset = 0

    with open(tmp_csv, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["last_name", "first_name", "city", "zip5", "date", "amount", "committee"])

        while True:
            params = {
                "$where": (
                    f"filing_sched_abbrev='A' "
                    f"AND flng_ent_zip>='{ZIP_MIN}' AND flng_ent_zip<='{ZIP_MAX}'"
                ),
                "$select": (
                    "flng_ent_last_name,flng_ent_first_name,"
                    "flng_ent_city,flng_ent_zip,sched_date,org_amt,cand_comm_name"
                ),
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
                last  = (r.get("flng_ent_last_name")  or "").strip().upper()
                first = (r.get("flng_ent_first_name") or "").strip().upper()
                city  = (r.get("flng_ent_city")       or "").strip().upper()
                zip5  = (r.get("flng_ent_zip")        or "").strip()[:5]
                date  = (r.get("sched_date")          or "")[:10]  # ISO date
                amt   = r.get("org_amt", "0") or "0"
                cmte  = (r.get("cand_comm_name")      or "").strip()
                if not last:
                    continue
                try:
                    amount = float(amt)
                except ValueError:
                    amount = 0.0
                writer.writerow([last, first, city, zip5, date, amount, cmte])
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
    """Build {last_name: [(name_norm, city, zip5, date, amount, committee)]} index."""
    index = defaultdict(list)
    total = 0
    print(f"  Indexing {csv_path.name}...")
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            last  = row["last_name"]
            first = row["first_name"]
            if not last:
                continue
            norm  = normalize_name(first, last)
            city  = row["city"]
            zip5  = row["zip5"]
            date  = row["date"]
            try:
                amount = float(row["amount"])
            except ValueError:
                amount = 0.0
            cmte = row["committee"]
            index[last].append((norm, city, zip5, date, amount, cmte))
            total += 1
    print(f"  Index: {len(index):,} unique last names, {total:,} total records")
    return index


# ---------- matching ----------------------------------------------------------

def classify(index, voter_name, voter_city, voter_zip5):
    last = voter_name.upper().split()[-1]
    v_tokens = frozenset(re.findall(r"[A-Z]+", voter_name.upper()))
    confirmed, possible = [], []
    for (name_norm, city, zip5, date, amount, cmte) in index.get(last, []):
        fec_tokens = frozenset(name_norm.split("|"))
        if not v_tokens.issubset(fec_tokens):
            continue
        record = {"date": date, "amount": amount, "committee": cmte}
        if city == voter_city.strip().upper() and zip5 == voter_zip5:
            confirmed.append(record)
        else:
            possible.append(record)
    return confirmed, possible[:POSSIBLE_CAP]


def iter_voters(filter_county=None, include_rep=False, all_parties=False):
    seen = set()
    for path in VOTER_SOURCES:
        df = pd.read_csv(path)
        df["zip_code"] = df["zip_code"].astype(str).str.strip().str[:5]
        if filter_county:
            df = df[df["county"].str.upper() == filter_county.upper()]
        for _, row in df.iterrows():
            people = parse_household(row.get("household_detail"))
            city = str(row.get("city") or "").strip().upper()
            zip5 = str(row.get("zip_code") or "").strip()
            for name, party, tier, age in priority_names(people, include_rep, all_parties):
                key = f"{name}|{city}|{zip5}"
                if key in seen:
                    continue
                seen.add(key)
                yield key, name, city, zip5


# ---------- main --------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-download", action="store_true",
                    help="use existing nyboe_contributions.csv, skip API fetch")
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
        print("=== Phase 1: Fetch NY BOE contributions ===")
        if not args.no_download:
            download_nyboe(NY_CSV, refilter=args.refilter)
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
        print(f"Existing NY BOE cache: {len(cache):,} entries")

        total = confirmed_new = possible_new = 0
        for key, name, city, zip5 in iter_voters(args.county, args.include_rep, args.all_parties):
            if args.limit and total >= args.limit:
                break
            total += 1

            confirmed, possible = classify(index, name, city, zip5)
            if confirmed or possible:
                entry = {
                    "confirmed": confirmed,
                    "possible":  possible,
                    "checked_at": now_iso(),
                    "source": "nyboe",
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
        print(f"  Confirmed (state-level): {confirmed_new:,}")
        print(f"  Possible-only: {possible_new:,}")
        if args.dry_run:
            print("  [DRY RUN — cache not written]")

    finally:
        release_lock()


if __name__ == "__main__":
    main()
