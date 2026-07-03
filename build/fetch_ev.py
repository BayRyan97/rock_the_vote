#!/usr/bin/env python3
"""
fetch_ev.py — Download NY EV registration data for Nassau/Suffolk and build zip-level scores.

Source: data.ny.gov "Electric Vehicle Registrations" (dataset 3vp6-cxmr)
Output: data/ev_zip_scores.json  →  {zip_code: 0–100}

Score is a log-normalized EV density rank across all Nassau+Suffolk zips.
Higher = more EVs registered in that zip = stronger environmental signal.

Usage:
    pip install requests
    python build/fetch_ev.py
"""
import json
import math
from collections import defaultdict
from pathlib import Path

import requests

ROOT = Path(__file__).parent.parent
DATA = ROOT / "data"
OUTPUT = DATA / "ev_zip_scores.json"
COUNTS_OUTPUT = DATA / "ev_zip_counts.json"

API_URL = "https://data.ny.gov/resource/3vp6-cxmr.json"
TARGET_COUNTIES = "county in('NASSAU','SUFFOLK')"
PAGE_SIZE = 50_000


def fetch_ev_data() -> list[dict]:
    rows, offset = [], 0
    while True:
        resp = requests.get(
            API_URL,
            params={
                "$where": TARGET_COUNTIES,
                "$select": "zip,county",
                "$limit": PAGE_SIZE,
                "$offset": offset,
            },
            timeout=60,
        )
        resp.raise_for_status()
        page = resp.json()
        if not page:
            break
        rows.extend(page)
        print(f"  fetched {len(rows):,} records...")
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return rows


def compute_scores(rows: list[dict]) -> tuple[dict[str, int], dict[str, int]]:
    zip_counts: dict[str, int] = defaultdict(int)
    for row in rows:
        z = str(row.get("zip", "")).strip().zfill(5)
        if z and z != "00000":
            zip_counts[z] += 1

    if not zip_counts:
        return {}, {}

    # Log scale handles the long-tailed distribution (a handful of zips have
    # 10x as many EVs as average; log keeps the low end of the scale meaningful)
    log_counts = {z: math.log1p(c) for z, c in zip_counts.items()}
    lo, hi = min(log_counts.values()), max(log_counts.values())

    scores = {
        z: (round((lc - lo) / (hi - lo) * 100) if hi > lo else 50)
        for z, lc in log_counts.items()
    }
    return scores, dict(zip_counts)


def main():
    print("Fetching Nassau+Suffolk EV registrations from data.ny.gov...")
    rows = fetch_ev_data()
    print(f"Total: {len(rows):,} EV registrations")

    print("Computing zip-level EV scores...")
    scores, zip_counts = compute_scores(rows)

    print(f"  {len(scores)} unique zip codes")
    print("  Top 10 zips by EV score:")
    for z, s in sorted(scores.items(), key=lambda x: -x[1])[:10]:
        print(f"    {z}  score={s:3d}  raw_count={zip_counts[z]:,}")

    OUTPUT.write_text(json.dumps(scores, sort_keys=True, indent=2))
    COUNTS_OUTPUT.write_text(json.dumps(zip_counts, sort_keys=True, indent=2))
    print(f"\nSaved → {OUTPUT.relative_to(ROOT)}")
    print(f"Saved → {COUNTS_OUTPUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
