#!/usr/bin/env python3
"""
fetch_fec.py — matches voters in Supabase (Nassau+Suffolk) to FEC Schedule A
(itemized individual contribution) records by name + city/state/zip. Voters
come from the live people/households tables via voter_source.py, not a local
Nassau.csv/Suffolk.csv export.

FEC doesn't expose a stable contributor ID, so this is necessarily a fuzzy
text match. Each result is classified:
  - "confirmed": a returned contribution's city AND zip match the voter's
    household address exactly.
  - "possible": the name matched (within NY) but city/zip didn't line up —
    could easily be a different person with the same name.

Runs concurrent requests against the FEC API (ThreadPoolExecutor) with a
shared rate limiter that keeps total throughput at ~55/min — safely under
the 60/min key limit. At ~50 effective req/min vs the prior sequential ~12/min,
typical runtime drops from ~9 days to ~2 days for a full 168k-voter seed.

Caches to data/fec_cache.json with a checked_at timestamp per entry so reruns
only re-query stale entries (confirmed donors every 30d, others every 90d).
A lock file (data/.fec_fetch.lock) prevents overlapping runs.

Usage:
    python build/fetch_fec.py                   # BLK/DEM-dropoff, top 5,000 households
    python build/fetch_fec.py --all-parties      # every registered voter (full seed)
    python build/fetch_fec.py --include-rep      # also include REP voters
    python build/fetch_fec.py --limit 25         # test on first 25 due-for-query people
    python build/fetch_fec.py --top-households 0 # no household cap
"""
import argparse
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter

import voter_source

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CACHE_FILE = DATA / "fec_cache.json"
LOCK_FILE  = DATA / ".fec_fetch.lock"
ENV_FILE   = ROOT / ".env"

API_BASE = "https://api.open.fec.gov/v1/schedules/schedule_a/"
TOP_N_HOUSEHOLDS = 5000

# Rate limiter: 55/min keeps us safely under the 60/min API key cap.
# With WORKERS concurrent in-flight requests, each worker waits for the
# limiter before firing — so total throughput approaches the rate limit
# instead of being throttled by individual response latency.
RATE_PER_MINUTE = 55
WORKERS = 8
REQUEST_TIMEOUT = 20  # seconds — city filter dramatically narrows FEC result sets
RETRIES = 2
STALE_DAYS_MATCHED  = 30
STALE_DAYS_NO_MATCH = 90
STALE_DAYS_TIMEOUT  = 7   # retry timed-out names after a week
SAVE_EVERY = 25

# A bare requests.get() opens and tears down a fresh Session (and TCP/TLS
# connection) on every call. Over tens of thousands of calls across a
# multi-hour run with WORKERS threads, that leaks sockets faster than the OS
# reclaims them and the run degrades into near-total stalls after an hour or
# two, independent of FEC's own uptime. One shared, pooled Session (safe to
# reuse across threads for reads) avoids that.
_session = requests.Session()
_session.mount("https://", HTTPAdapter(pool_connections=WORKERS, pool_maxsize=WORKERS))


# ---------- helpers ----------------------------------------------------------

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


def needs_query(entry):
    if entry is None:
        return True
    if entry.get("timeout"):
        return days_since(entry.get("checked_at")) >= STALE_DAYS_TIMEOUT
    has_match = bool(entry.get("confirmed") or entry.get("possible"))
    threshold = STALE_DAYS_MATCHED if has_match else STALE_DAYS_NO_MATCH
    return days_since(entry.get("checked_at")) >= threshold


# ---------- lock file --------------------------------------------------------

def acquire_lock():
    if LOCK_FILE.exists():
        age_min = (time.time() - LOCK_FILE.stat().st_mtime) / 60
        sys.exit(
            f"Another fetch_fec.py run appears to be in progress "
            f"(lock file is {age_min:.0f} min old). If that's wrong, "
            f"delete {LOCK_FILE} and retry."
        )
    LOCK_FILE.write_text(str(os.getpid()))


def release_lock():
    LOCK_FILE.unlink(missing_ok=True)


# ---------- API key ----------------------------------------------------------

def load_api_key():
    key = os.environ.get("FEC_API_KEY")
    if key:
        return key
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            if line.startswith("FEC_API_KEY="):
                return line.split("=", 1)[1].strip()
    sys.exit("FEC_API_KEY not set. Export it or put FEC_API_KEY=... in .env")


# ---------- FEC likelihood scoring --------------------------------------------

def fec_likelihood(zip5, party, tier, age, zip_hits, zip_totals):
    """Score how likely this person is to have an FEC record.
    Primary: zip confirmed-match rate from existing cache data.
    Secondary: engagement tier (X = votes everywhere = more engaged donor),
               age (older voters give more), party.
    Unknown zips get a moderate default so we still explore them."""
    total = zip_totals.get(zip5, 0)
    hits  = zip_hits.get(zip5, 0)
    if total >= 10:
        zip_rate = hits / total
    elif total > 0:
        # Smooth toward a moderate prior for low-sample zips
        zip_rate = (hits + 2) / (total + 10)
    else:
        zip_rate = 0.05  # unknown zip — moderate default

    score = zip_rate * 100

    # Engagement tier: X-class voters are most politically active
    if tier and tier.startswith("X"):
        score += 4
    elif tier and tier.startswith("F"):
        score += 1

    # Age: donors skew older (need disposable income, $200+ threshold)
    if age and age >= 65:
        score += 3
    elif age and age >= 50:
        score += 2
    elif age and age >= 40:
        score += 1

    return score


# ---------- cache ------------------------------------------------------------

def load_cache():
    if CACHE_FILE.exists():
        return json.loads(CACHE_FILE.read_text())
    return {}


def save_cache(cache, lock):
    with lock:
        tmp = CACHE_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(cache))
        tmp.replace(CACHE_FILE)


# ---------- FEC API ----------------------------------------------------------

def fec_query(api_key, name, state, city=None):
    params = {
        "api_key": api_key,
        "contributor_name": name,
        "contributor_state": state,
        "per_page": 30,
        "sort": "-contribution_receipt_date",
    }
    if city:
        params["contributor_city"] = city
    for attempt in range(RETRIES + 1):
        try:
            resp = _session.get(API_BASE, params=params, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 429:
                time.sleep(65)
                continue
            resp.raise_for_status()
            remaining = resp.headers.get("x-ratelimit-remaining")
            return resp.json(), (int(remaining) if remaining is not None else None)
        except requests.Timeout:
            # Timeouts mean FEC's server is slow on this name (large result set).
            # Retrying just wastes time — skip and the monthly refresh will retry.
            raise
        except requests.RequestException as e:
            if attempt < RETRIES:
                time.sleep(2 ** attempt)
            else:
                raise


def classify(records, city, zip5):
    confirmed, possible = [], []
    for r in records:
        item = {
            "date":       r.get("contribution_receipt_date"),
            "amount":     r.get("contribution_receipt_amount"),
            "committee":  (r.get("committee") or {}).get("name"),
            "employer":   r.get("contributor_employer"),
            "occupation": r.get("contributor_occupation"),
        }
        rec_city = (r.get("contributor_city") or "").strip().upper()
        rec_zip  = (r.get("contributor_zip")  or "")[:5]
        if rec_city and city and rec_city == city.strip().upper() and rec_zip == zip5:
            confirmed.append(item)
        else:
            possible.append(item)
    return confirmed, possible[:10]


# ---------- rate limiter -----------------------------------------------------

class RateLimiter:
    """Token-bucket rate limiter safe for use across multiple threads."""
    def __init__(self, per_minute):
        self.interval = 60.0 / per_minute
        self._lock = threading.Lock()
        self._last = 0.0

    def acquire(self):
        with self._lock:
            now = time.monotonic()
            wait = self._last + self.interval - now
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()


# ---------- task list --------------------------------------------------------

def build_task_list(cache, top_n_households=TOP_N_HOUSEHOLDS,
                    include_rep=False, all_parties=False,
                    filter_county=None, filter_district=None):
    # Build zip-level match stats from the existing cache so we can prioritize
    # people in zip codes where we already know donors exist.
    from collections import defaultdict
    zip_hits   = defaultdict(int)
    zip_totals = defaultdict(int)
    for key, entry in cache.items():
        parts = key.split("|")
        if len(parts) != 3:
            continue
        z = parts[2]
        zip_totals[z] += 1
        if entry.get("confirmed"):
            zip_hits[z] += 1

    scope = f"AD{filter_district} {filter_county}" if filter_district else "full Nassau+Suffolk"
    print(f"Querying voters from Supabase and building FEC-likelihood-ordered task list ({scope})...")
    tasks_raw = []
    for v in voter_source.query_voters(filter_county=filter_county, filter_district=filter_district,
                                        include_rep=include_rep, all_parties=all_parties):
        key = v["donor_key"]
        if not needs_query(cache.get(key)):
            continue
        score = fec_likelihood(v["zip5"], v["party"], v["tier"], v["age"], zip_hits, zip_totals)
        tasks_raw.append((score, key, v["name"], v["city"], v["zip5"]))

    # Sort highest FEC likelihood first — puts known high-match zip codes
    # (Mill Neck, Old Westbury, Oyster Bay, etc.) ahead of low-match areas.
    tasks_raw.sort(key=lambda x: -x[0])
    tasks = [(key, name, city, zip5) for _, key, name, city, zip5 in tasks_raw]
    print(f"  {len(tasks)} people to query, ordered by FEC donation likelihood.")
    return tasks


# ---------- main -------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None,
                    help="only query the first N due-for-query people (testing)")
    ap.add_argument("--top-households", type=int, default=None,
                    help="household cap by canvass score (0/omit = all)")
    ap.add_argument("--include-rep", action="store_true",
                    help="also match Republican-registered voters")
    ap.add_argument("--all-parties", action="store_true",
                    help="match every registered voter regardless of party")
    ap.add_argument("--county", type=str, default=None,
                    help="filter to a single county (NASSAU or SUFFOLK)")
    ap.add_argument("--district", type=int, default=None,
                    help="filter to a single assembly district number")
    args = ap.parse_args()

    top_n = args.top_households
    if top_n is None:
        top_n = 0 if (args.include_rep or args.all_parties) else TOP_N_HOUSEHOLDS

    acquire_lock()
    try:
        api_key = load_api_key()
        cache   = load_cache()
        tasks   = build_task_list(cache, top_n_households=top_n or None,
                                  include_rep=args.include_rep,
                                  all_parties=args.all_parties,
                                  filter_county=args.county,
                                  filter_district=args.district)

        print(f"{len(cache)} people in cache. {len(tasks)} due for query "
              f"(matched every {STALE_DAYS_MATCHED}d, no-match every {STALE_DAYS_NO_MATCH}d).")

        if args.limit:
            tasks = tasks[:args.limit]
            print(f"--limit: querying {len(tasks)} people.")

        if not tasks:
            print("Nothing to do — all entries are fresh.")
            return

        rate_limiter = RateLimiter(RATE_PER_MINUTE)
        cache_lock   = threading.Lock()
        print_lock   = threading.Lock()
        hits = 0
        done = 0
        total = len(tasks)

        # Watchdog: a dead-but-still-ESTABLISHED TCP connection (packets
        # silently dropped, no RST) leaves a worker blocked in a C-level
        # socket read that no requests/urllib3-level timeout can interrupt --
        # observed via lsof during a real multi-hour stall where every
        # worker was ESTABLISHED but nothing had completed in over an hour.
        # Python cannot cleanly cancel a thread blocked like that, so the
        # only reliable recovery is to kill the whole process and let an
        # external retry loop start a fresh one.
        WATCHDOG_STALL_SECONDS = 300
        last_progress = [time.monotonic()]

        def watchdog():
            while True:
                time.sleep(20)
                if time.monotonic() - last_progress[0] > WATCHDOG_STALL_SECONDS:
                    with print_lock:
                        print(f"  WATCHDOG: no progress in {WATCHDOG_STALL_SECONDS}s "
                              f"({done}/{total} done) — likely dead connections stuck "
                              f"below the request timeout. Exiting for external restart.")
                    os._exit(1)

        threading.Thread(target=watchdog, daemon=True).start()

        def fetch_one(task):
            key, name, city, zip5 = task
            rate_limiter.acquire()
            try:
                data, remaining = fec_query(api_key, name, "NY", city=city)
            except requests.Timeout:
                with print_lock:
                    print(f"  TIMEOUT {name} — caching for {STALE_DAYS_TIMEOUT}d")
                return key, {"confirmed": [], "possible": [], "timeout": True, "checked_at": now_iso()}
            except requests.RequestException as e:
                with print_lock:
                    print(f"  ERROR {name}: {e}")
                return key, None
            confirmed, possible = classify(data.get("results", []), city, zip5)
            entry = {"confirmed": confirmed, "possible": possible, "checked_at": now_iso()}
            return key, entry

        with ThreadPoolExecutor(max_workers=WORKERS) as executor:
            futures = {executor.submit(fetch_one, t): t for t in tasks}
            for future in as_completed(futures):
                key, entry = future.result()
                done += 1
                last_progress[0] = time.monotonic()
                if entry is not None:
                    with cache_lock:
                        cache[key] = entry
                    if entry["confirmed"] or entry["possible"]:
                        hits += 1
                        name, city, zip5 = key.split("|")
                        with print_lock:
                            print(f"  [{done}/{total}] {name} ({city}, {zip5}): "
                                  f"{len(entry['confirmed'])} confirmed, "
                                  f"{len(entry['possible'])} possible")
                if done % SAVE_EVERY == 0:
                    save_cache(cache, cache_lock)
                    with print_lock:
                        print(f"  ...checkpoint {done}/{total}")

        save_cache(cache, cache_lock)
        print(f"Done. {hits}/{total} had at least one match. "
              f"Cache now has {len(cache)} entries.")
    finally:
        release_lock()


if __name__ == "__main__":
    main()
