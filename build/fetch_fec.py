#!/usr/bin/env python3
"""
fetch_fec.py — matches voters in the Nassau County file to FEC Schedule A
(itemized individual contribution) records by name + city/state/zip.

FEC doesn't expose a stable contributor ID, so this is necessarily a fuzzy
text match. Each result is classified:
  - "confirmed": a returned contribution's city AND zip match the voter's
    household address exactly.
  - "possible": the name matched (within NY) but city/zip didn't line up —
    could easily be a different person with the same name.

Caches to data/fec_cache.json, keyed by "NAME|CITY|ZIP", with a checked_at
timestamp per entry so this script is safe to rerun on a schedule:
  - people never queried before: always queried.
  - people with an existing match: re-queried after STALE_DAYS_MATCHED days
    (their donation history can grow).
  - people with no match found: re-queried after STALE_DAYS_NO_MATCH days
    (cheaper — most people who've never donated stay that way).

A lock file (data/.fec_fetch.lock) prevents two runs overlapping and
corrupting the cache (e.g. a scheduled refresh firing while a manual run
is still going).

Scope: only priority voters (BLK/unaffiliated or drop-off-tier DEM) within the
top-N highest-scored households, not the whole voter file — see TOP_N_HOUSEHOLDS.

Usage:
    python build/fetch_fec.py                       # top 5,000 households, resumes + refreshes stale entries
    python build/fetch_fec.py --limit 25             # test on first 25 due-for-query people
    python build/fetch_fec.py --top-households 10000 # widen the household pool
    python build/fetch_fec.py --top-households 0     # no household cap, all priority voters
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
VOTER_FILE = DATA / "Assembly_15_13.xlsx"
CACHE_FILE = DATA / "fec_cache.json"
LOCK_FILE = DATA / ".fec_fetch.lock"
ENV_FILE = ROOT / ".env"

PERSON_PATTERN = re.compile(r"^(.*) \((\d+), ([A-Z]+), ([A-Z0-9]+)\)$")
API_BASE = "https://api.open.fec.gov/v1/schedules/schedule_a/"
DROPOFF_TIERS = {"I0", "F1", "L1"}
LOW_TIERS = {"I0", "F1", "L1", "F2", "L2"}
TOP_N_HOUSEHOLDS = 5000  # highest-scored households only — see README for rationale

STALE_DAYS_MATCHED = 30   # re-check people with a known donation history monthly
STALE_DAYS_NO_MATCH = 90  # re-check people with no history quarterly — cheaper, lower payoff

# The key's real limit is 60/min (checked via response headers), but actual
# throughput is dominated by FEC's response latency, not this interval.
MIN_INTERVAL = 1.2
SAVE_EVERY = 25


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def days_since(iso_str):
    if not iso_str:
        return float("inf")
    try:
        then = datetime.fromisoformat(iso_str)
    except ValueError:
        return float("inf")
    return (datetime.now(timezone.utc) - then).total_seconds() / 86400


def acquire_lock():
    if LOCK_FILE.exists():
        age_min = (time.time() - LOCK_FILE.stat().st_mtime) / 60
        sys.exit(f"Another fetch_fec.py run appears to be in progress "
                  f"(lock file is {age_min:.0f} min old). If that's wrong, "
                  f"delete {LOCK_FILE} and retry.")
    LOCK_FILE.write_text(str(os.getpid()))


def release_lock():
    LOCK_FILE.unlink(missing_ok=True)


def load_api_key():
    key = os.environ.get("FEC_API_KEY")
    if key:
        return key
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            if line.startswith("FEC_API_KEY="):
                return line.split("=", 1)[1].strip()
    sys.exit("FEC_API_KEY not set. Export it or put FEC_API_KEY=... in .env")


def parse_household(detail):
    """Returns (name, party, tier) for every household member."""
    if not isinstance(detail, str) or not detail.strip():
        return []
    people = []
    for entry in detail.split(" | "):
        m = PERSON_PATTERN.match(entry.strip())
        if m:
            people.append((m.group(1), m.group(3), m.group(4)))
    return people


def score_household(people):
    """Mirrors build.py's score_household — needed to rank households without
    re-running the full geocoding pipeline just to pick the top N."""
    if not people:
        return 0
    votes = [int(t[1:]) if len(t) > 1 and t[1:].isdigit() else 0 for _, _, t in people]
    gap = max(votes) - min(votes)
    num_low = sum(1 for _, _, t in people if t in LOW_TIERS)
    num_blk = sum(1 for _, p, _ in people if p == "BLK")
    num_dem_drop = sum(1 for _, p, t in people if p == "DEM" and t in DROPOFF_TIERS)
    return gap * num_low + num_blk * 2 + num_dem_drop


def priority_names(people, include_rep=False, all_parties=False):
    """Returns names of voters to match against FEC.
    Default: BLK (unaffiliated) and drop-off-tier DEM — the canvass targets.
    include_rep=True: also includes all REP-registered voters.
    all_parties=True: every registered voter regardless of party."""
    if all_parties:
        return [name for name, _, _ in people]
    out = []
    for name, party, tier in people:
        if party == "BLK" or (party == "DEM" and tier in DROPOFF_TIERS):
            out.append(name)
        elif include_rep and party == "REP":
            out.append(name)
    return out


def load_cache():
    if CACHE_FILE.exists():
        return json.loads(CACHE_FILE.read_text())
    return {}


def save_cache(cache):
    tmp = CACHE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cache))
    tmp.replace(CACHE_FILE)


def needs_query(entry):
    if entry is None:
        return True
    has_match = bool(entry.get("confirmed") or entry.get("possible"))
    threshold = STALE_DAYS_MATCHED if has_match else STALE_DAYS_NO_MATCH
    return days_since(entry.get("checked_at")) >= threshold


def fec_query(api_key, name, state):
    params = {
        "api_key": api_key,
        "contributor_name": name,
        "contributor_state": state,
        "per_page": 30,
        "sort": "-contribution_receipt_date",
    }
    resp = requests.get(API_BASE, params=params, timeout=20)
    if resp.status_code == 429:
        time.sleep(65)
        resp = requests.get(API_BASE, params=params, timeout=20)
    resp.raise_for_status()
    remaining = resp.headers.get("x-ratelimit-remaining")
    return resp.json(), (int(remaining) if remaining is not None else None)


def classify(records, city, zip5):
    confirmed, possible = [], []
    for r in records:
        item = {
            "date": r.get("contribution_receipt_date"),
            "amount": r.get("contribution_receipt_amount"),
            "committee": (r.get("committee") or {}).get("name"),
        }
        rec_city = (r.get("contributor_city") or "").strip().upper()
        rec_zip = (r.get("contributor_zip") or "")[:5]
        if rec_city and city and rec_city == city.strip().upper() and rec_zip == zip5:
            confirmed.append(item)
        else:
            possible.append(item)
    # cap possible matches — they're context, not a full audit trail
    return confirmed, possible[:10]


def build_task_list(cache, top_n_households=TOP_N_HOUSEHOLDS, include_rep=False, all_parties=False):
    print("Loading voter file...")
    df = pd.read_excel(VOTER_FILE)
    df["zip_code"] = df["zip_code"].astype(str).str.strip().str[:5]

    print(f"Scoring households to find the top {top_n_households}...")
    scored = []
    for _, row in df.iterrows():
        people = parse_household(row.get("household_detail"))
        scored.append((score_household(people), row, people))
    scored.sort(key=lambda x: -x[0])
    if top_n_households:
        scored = scored[:top_n_households]
    min_score = scored[-1][0] if scored else 0
    print(f"Top {len(scored)} households span scores {scored[0][0] if scored else 0} down to {min_score}.")

    tasks, seen = [], set()
    for _, row, people in scored:
        city = str(row.get("city") or "").strip()
        zip5 = str(row.get("zip_code") or "").strip()
        for name in priority_names(people, include_rep=include_rep, all_parties=all_parties):
            key = f"{name}|{city}|{zip5}"
            if key in seen:
                continue
            seen.add(key)
            if not needs_query(cache.get(key)):
                continue
            tasks.append((key, name, city, zip5))
    return tasks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="only query the first N due-for-query people")
    ap.add_argument("--top-households", type=int, default=None,
                     help="only consider voters in the N highest-scored households (0/omit = all households)")
    ap.add_argument("--include-rep", action="store_true",
                     help="also match Republican-registered voters")
    ap.add_argument("--all-parties", action="store_true",
                     help="match every registered voter regardless of party (~153k new, ~8.8 day initial run)")
    args = ap.parse_args()

    # Default to no household cap when covering all parties or REP voters
    # (the canvass-score cap is designed for BLK/DEM targeting only)
    top_n = args.top_households
    if top_n is None:
        top_n = 0 if (args.include_rep or args.all_parties) else TOP_N_HOUSEHOLDS

    acquire_lock()
    try:
        api_key = load_api_key()
        cache = load_cache()
        tasks = build_task_list(cache, top_n_households=top_n or None,
                                include_rep=args.include_rep, all_parties=args.all_parties)
        print(f"{len(cache)} people in cache. {len(tasks)} due for query "
              f"(new, or stale — matched people recheck every {STALE_DAYS_MATCHED}d, "
              f"no-match people every {STALE_DAYS_NO_MATCH}d).")

        if args.limit:
            tasks = tasks[: args.limit]
            print(f"--limit set: querying {len(tasks)} people this run.")

        last_call = 0.0
        hits = 0
        for i, (key, name, city, zip5) in enumerate(tasks):
            elapsed = time.time() - last_call
            if elapsed < MIN_INTERVAL:
                time.sleep(MIN_INTERVAL - elapsed)
            last_call = time.time()

            try:
                data, remaining = fec_query(api_key, name, "NY")
            except requests.RequestException as e:
                print(f"  [{i}] {name}: ERROR {e}")
                continue

            if remaining is not None and remaining < 5:
                print(f"  rate limit nearly exhausted ({remaining} left) — pausing 65s")
                time.sleep(65)

            confirmed, possible = classify(data.get("results", []), city, zip5)
            cache[key] = {"confirmed": confirmed, "possible": possible, "checked_at": now_iso()}
            if confirmed or possible:
                hits += 1
                print(f"  [{i+1}/{len(tasks)}] {name} ({city}, {zip5}): "
                      f"{len(confirmed)} confirmed, {len(possible)} possible")

            if (i + 1) % SAVE_EVERY == 0:
                save_cache(cache)
                print(f"  ...saved checkpoint at {i+1}/{len(tasks)}")

        save_cache(cache)
        print(f"Done. {hits}/{len(tasks)} people had at least one match this run. "
              f"Cache now has {len(cache)} entries total.")
    finally:
        release_lock()


if __name__ == "__main__":
    main()
