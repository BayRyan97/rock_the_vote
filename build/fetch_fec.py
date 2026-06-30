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

Caches incrementally to data/fec_cache.json, keyed by "NAME|CITY|ZIP", so
the run can be killed and resumed without re-querying people already done.

Scope: only priority voters (BLK/unaffiliated or drop-off-tier DEM) within the
top-N highest-scored households, not the whole voter file — see TOP_N_HOUSEHOLDS.

Usage:
    python build/fetch_fec.py                       # top 5,000 households (resumes from cache)
    python build/fetch_fec.py --limit 25             # test on first 25 unqueried people
    python build/fetch_fec.py --top-households 10000 # widen the household pool
    python build/fetch_fec.py --top-households 0     # no household cap, all priority voters
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
VOTER_FILE = DATA / "Assembly_15_13.xlsx"
CACHE_FILE = DATA / "fec_cache.json"
ENV_FILE = ROOT / ".env"

PERSON_PATTERN = re.compile(r"^(.*) \((\d+), ([A-Z]+), ([A-Z0-9]+)\)$")
API_BASE = "https://api.open.fec.gov/v1/schedules/schedule_a/"
DROPOFF_TIERS = {"I0", "F1", "L1"}
LOW_TIERS = {"I0", "F1", "L1", "F2", "L2"}
TOP_N_HOUSEHOLDS = 5000  # highest-scored households only — see README for rationale

# The key's real limit is 60/min (checked via response headers). Stay safely
# under it and back off harder if the API ever says we're close to empty.
MIN_INTERVAL = 1.2
SAVE_EVERY = 25


def load_api_key():
    import os
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


def priority_names(people):
    """BLK (unaffiliated) or drop-off-tier DEM — the people the canvass score
    already treats as persuasion targets."""
    return [name for name, party, tier in people
            if party == "BLK" or (party == "DEM" and tier in DROPOFF_TIERS)]


def load_cache():
    if CACHE_FILE.exists():
        return json.loads(CACHE_FILE.read_text())
    return {}


def save_cache(cache):
    CACHE_FILE.write_text(json.dumps(cache))


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


def build_task_list(top_n_households=TOP_N_HOUSEHOLDS):
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

    cache = load_cache()
    tasks, seen = [], set()
    for _, row, people in scored:
        city = str(row.get("city") or "").strip()
        zip5 = str(row.get("zip_code") or "").strip()
        for name in priority_names(people):
            key = f"{name}|{city}|{zip5}"
            if key in seen:
                continue
            seen.add(key)
            if key in cache:
                continue
            tasks.append((key, name, city, zip5))
    return cache, tasks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="only query the first N un-cached people")
    ap.add_argument("--top-households", type=int, default=TOP_N_HOUSEHOLDS,
                     help="only consider priority voters in the N highest-scored households (0 = all)")
    args = ap.parse_args()

    api_key = load_api_key()
    cache, tasks = build_task_list(top_n_households=args.top_households or None)
    print(f"{len(cache)} people already cached. {len(tasks)} remaining to query.")

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
        cache[key] = {"confirmed": confirmed, "possible": possible}
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


if __name__ == "__main__":
    main()
