#!/usr/bin/env python3
"""
fetch_fec_bulk.py — matches voters against FEC Schedule A data via bulk download.

Instead of querying the FEC API one person at a time (1.8M queries, months of runtime),
this script downloads FEC's bulk individual-contribution ZIP files, filters to NY-only
rows on the fly, builds a local name index, and matches all voters in ~30-60 minutes.

The NY-filtered CSVs are saved to data/fec_ny_{cycle}.csv so subsequent runs reuse
them without re-downloading (skip with --no-download, force refresh with --refilter).

Cache output format is identical to fetch_fec.py so build.py needs no changes.

Usage:
    python build/fetch_fec_bulk.py                              # 2024 cycle, BLK+DEM dropoff
    python build/fetch_fec_bulk.py --all-parties                # all registered voters
    python build/fetch_fec_bulk.py --cycles 2018 2020 2022 2024 # full history (~90 min)
    python build/fetch_fec_bulk.py --no-download                # use existing NY CSVs
    python build/fetch_fec_bulk.py --limit 1000 --dry-run       # smoke test
"""
import argparse
import csv
import io
import json
import os
import re
import sys
import time
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
VOTER_SOURCES = [DATA / "Nassau.csv", DATA / "Suffolk.csv"]
CACHE_FILE = DATA / "fec_cache.json"
LOCK_FILE  = DATA / ".fec_fetch.lock"

PERSON_PATTERN = re.compile(r"^(.*) \((\d+), ([A-Z]+), ([A-Z0-9]+)\)$")
DROPOFF_TIERS  = {"I0", "F1", "L1"}

POSSIBLE_CAP = 10

# FEC pipe-delimited field indices (Schedule A individual contributions)
IDX_CMTE_ID = 0
IDX_NAME    = 7
IDX_CITY    = 8
IDX_STATE   = 9
IDX_ZIP     = 10
IDX_DATE    = 13
IDX_AMT     = 14

# Committee master field indices
CM_IDX_CMTE_ID = 0
CM_IDX_CMTE_NM = 1

BULK_BASE = "https://www.fec.gov/files/bulk-downloads"


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


def format_date(raw):
    raw = raw.strip()
    if len(raw) == 8 and raw.isdigit():
        return f"{raw[4:]}-{raw[:2]}-{raw[2:4]}"
    return ""


def normalize_name(fec_name):
    """'RUBENSTEIN, BARRY M' → 'BARRY|M|RUBENSTEIN' (sorted alpha tokens, pipe-joined)"""
    return "|".join(sorted(re.findall(r"[A-Z]+", fec_name.upper())))


def get_last_name(fec_name):
    """'RUBENSTEIN, BARRY M' → 'RUBENSTEIN' (index lookup key)"""
    return fec_name.split(",", 1)[0].strip().upper()


# ---------- lock file ---------------------------------------------------------

def acquire_lock():
    if LOCK_FILE.exists():
        age_min = (time.time() - LOCK_FILE.stat().st_mtime) / 60
        sys.exit(
            f"Another fec fetch run appears to be in progress "
            f"(lock file is {age_min:.0f} min old). "
            f"If that's wrong, delete {LOCK_FILE} and retry."
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

def download_streaming(url, dest_path, desc=""):
    """Stream download url to dest_path with progress output."""
    print(f"  Downloading {desc or url} ...")
    with requests.get(url, stream=True, timeout=60) as resp:
        resp.raise_for_status()
        total = int(resp.headers.get("content-length", 0))
        downloaded = 0
        last_print = 0
        with open(dest_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8 * 1024 * 1024):
                f.write(chunk)
                downloaded += len(chunk)
                if total and downloaded - last_print >= 100 * 1024 * 1024:
                    pct = downloaded / total * 100
                    print(f"    {downloaded // (1024*1024)} MB / {total // (1024*1024)} MB ({pct:.0f}%)")
                    last_print = downloaded
    print(f"    Done ({downloaded // (1024*1024)} MB)")


def load_committee_names(cycles):
    """Download committee master files and return {cmte_id: cmte_name}."""
    cmte_names = {}
    for cycle in cycles:
        yy = str(cycle)[-2:]
        url = f"{BULK_BASE}/{cycle}/cm{yy}.zip"
        print(f"  Loading committee names for {cycle}...")
        try:
            resp = requests.get(url, timeout=60)
            resp.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                inner = max(zf.infolist(), key=lambda i: i.file_size).filename
                with zf.open(inner) as f:
                    for line in io.TextIOWrapper(f, encoding="latin-1", errors="replace"):
                        fields = line.rstrip("\n").split("|")
                        if len(fields) >= 2:
                            cmte_names[fields[CM_IDX_CMTE_ID].strip()] = fields[CM_IDX_CMTE_NM].strip()
        except Exception as e:
            print(f"    WARNING: could not load committee names for {cycle}: {e}")
    print(f"  {len(cmte_names):,} committee names loaded")
    return cmte_names


def download_and_filter_ny(cycle, dest_csv, refilter=False):
    """Download Schedule A bulk zip for cycle, filter to NY, write dest_csv.
    Returns number of NY rows written. Skips download if dest_csv exists."""
    if dest_csv.exists() and not refilter:
        size_mb = dest_csv.stat().st_size // (1024 * 1024)
        print(f"  Using existing {dest_csv.name} ({size_mb} MB) — pass --refilter to re-download")
        with open(dest_csv) as f:
            return sum(1 for _ in f)

    yy = str(cycle)[-2:]
    url = f"{BULK_BASE}/{cycle}/indiv{yy}.zip"
    tmp_zip = DATA / f"fec_tmp_{cycle}.zip"
    tmp_csv = dest_csv.with_suffix(".csv.tmp")

    try:
        download_streaming(url, tmp_zip, desc=f"indiv{yy}.zip ({cycle} individual contributions)")

        print(f"  Filtering to NY rows...")
        row_count = 0
        with zipfile.ZipFile(tmp_zip) as zf:
            inner = max(zf.infolist(), key=lambda i: i.file_size).filename
            print(f"    Inner file: {inner}")
            with zf.open(inner) as raw:
                reader = io.TextIOWrapper(raw, encoding="latin-1", errors="replace")
                with open(tmp_csv, "w", newline="") as out:
                    writer = csv.writer(out)
                    for line in reader:
                        fields = line.rstrip("\n").split("|")
                        if len(fields) < 15:
                            continue
                        if fields[IDX_STATE].strip().upper() != "NY":
                            continue
                        writer.writerow([
                            fields[IDX_CMTE_ID].strip(),
                            fields[IDX_NAME].strip(),
                            fields[IDX_CITY].strip().upper(),
                            fields[IDX_ZIP].strip()[:5],
                            fields[IDX_DATE].strip(),
                            fields[IDX_AMT].strip(),
                        ])
                        row_count += 1
                        if row_count % 500_000 == 0:
                            print(f"    {row_count:,} NY rows so far...")

        tmp_csv.rename(dest_csv)
        print(f"  {row_count:,} NY rows written to {dest_csv.name}")
        return row_count

    finally:
        tmp_zip.unlink(missing_ok=True)


# ---------- index builder -----------------------------------------------------

def build_name_index(ny_csv_paths, committee_names):
    """Build {last_name: [(name_norm, city, zip5, date, amount, cmte_name)]} index."""
    index = defaultdict(list)
    total = 0
    for path in ny_csv_paths:
        if not path.exists():
            print(f"  WARNING: {path} not found, skipping")
            continue
        print(f"  Indexing {path.name}...")
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.reader(f):
                if len(row) < 6:
                    continue
                cmte_id, fec_name, city, zip5, date_raw, amt_raw = row
                if not fec_name:
                    continue
                try:
                    amount = float(amt_raw) if amt_raw else 0.0
                except ValueError:
                    amount = 0.0
                cmte_name = committee_names.get(cmte_id, cmte_id)
                last = get_last_name(fec_name)
                norm = normalize_name(fec_name)
                index[last].append((norm, city, zip5, format_date(date_raw), amount, cmte_name))
                total += 1
        print(f"    {total:,} total records indexed")

    print(f"  Index complete: {len(index):,} unique last names, {total:,} total NY records")
    return index


# ---------- voter matching ----------------------------------------------------

def classify_bulk(index, voter_name, voter_city, voter_zip5):
    """Match voter against FEC index. Returns (confirmed, possible) lists."""
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
    """Yield (cache_key, name, city, zip5) for each priority voter, deduplicated."""
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
    ap.add_argument("--cycles", type=int, nargs="+", default=[2024],
                    help="election cycles to download (default: 2024)")
    ap.add_argument("--no-download", action="store_true",
                    help="skip download, use existing fec_ny_*.csv files")
    ap.add_argument("--refilter", action="store_true",
                    help="re-download and re-filter even if NY CSV already exists")
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
        # Phase 1: Download & filter
        ny_csv_paths = []
        if args.no_download:
            for cycle in args.cycles:
                p = DATA / f"fec_ny_{cycle}.csv"
                ny_csv_paths.append(p)
                if not p.exists():
                    print(f"WARNING: {p} does not exist — run without --no-download first")
        else:
            print("=== Phase 1: Download & filter to NY ===")
            committee_names = load_committee_names(args.cycles)
            for cycle in sorted(args.cycles):
                dest = DATA / f"fec_ny_{cycle}.csv"
                download_and_filter_ny(cycle, dest, refilter=args.refilter)
                ny_csv_paths.append(dest)

        if not any(p.exists() for p in ny_csv_paths):
            sys.exit("No NY CSV files found. Run without --no-download.")

        if args.no_download:
            committee_names = {}

        # Phase 2: Build name index
        print("\n=== Phase 2: Build name index ===")
        index = build_name_index(ny_csv_paths, committee_names)

        # Phase 3: Match voters
        print("\n=== Phase 3: Match voters ===")
        cache = load_cache()
        print(f"Existing cache: {len(cache):,} entries, "
              f"{sum(1 for v in cache.values() if v.get('confirmed')):,} confirmed")

        total = confirmed_new = possible_new = preserved = 0
        for key, name, city, zip5 in iter_voters(args.county, args.include_rep, args.all_parties):
            if args.limit and total >= args.limit:
                break
            total += 1

            existing = cache.get(key)
            # Preserve entries confirmed by the API (not from a previous bulk run)
            if existing and existing.get("confirmed") and existing.get("source") != "bulk":
                preserved += 1
                continue

            confirmed, possible = classify_bulk(index, name, city, zip5)

            # Only write if we found something (keeps cache file small)
            if confirmed or possible:
                entry = {
                    "confirmed": confirmed,
                    "possible":  possible,
                    "checked_at": now_iso(),
                    "source": "bulk",
                }
                if not args.dry_run:
                    cache[key] = entry
                if confirmed:
                    confirmed_new += 1
                elif possible:
                    possible_new += 1

            if total % 100_000 == 0:
                print(f"  {total:,} voters processed | "
                      f"{confirmed_new:,} confirmed | {possible_new:,} possible")

        # Phase 4: Save
        if not args.dry_run:
            print("\n=== Phase 4: Save cache ===")
            save_cache(cache)
            print(f"  Saved {len(cache):,} entries to {CACHE_FILE.name}")

        elapsed = time.monotonic() - t_start
        print(f"\n=== Done in {elapsed/60:.1f} min ===")
        print(f"  Cycles: {sorted(args.cycles)}")
        print(f"  Voters processed: {total:,}")
        print(f"  Confirmed (new/updated): {confirmed_new:,}")
        print(f"  Possible-only (new/updated): {possible_new:,}")
        print(f"  API-confirmed (preserved): {preserved:,}")
        if args.dry_run:
            print("  [DRY RUN — cache not written]")

    finally:
        release_lock()


if __name__ == "__main__":
    main()
