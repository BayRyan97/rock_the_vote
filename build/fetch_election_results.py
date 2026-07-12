#!/usr/bin/env python3
"""
fetch_election_results.py — Download Long Island election results from the
OpenElections project (openelections/openelections-data-ny on GitHub) and write
data/election_results.json.

Currently provides 2020 + 2022 general election results for State Assembly,
State Senate, and U.S. House from Nassau and Suffolk counties.

2024 results were sourced from Wikipedia / NY State BOE certified results
(elections.ny.gov, approved 12/09/2024) and are preserved in data/election_results.json.
The script preserves any existing non-zero 2024 entries rather than overwriting them.

Usage:
    pip install requests
    python build/fetch_election_results.py            # fetch + scaffold
    python build/fetch_election_results.py --validate # check completeness
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

import requests

ROOT = Path(__file__).parent.parent
DATA = ROOT / "data"
OUTPUT = DATA / "election_results.json"

YEARS = ["2024", "2022", "2020"]

# OpenElections GitHub raw file base
OE_RAW = "https://raw.githubusercontent.com/openelections/openelections-data-ny/master"

# Files to fetch per year (county-level general election precinct files)
# 2024: OpenElections does not yet have NY precinct-level data. When it arrives,
# add entries like: "2024/counties/20241105__ny__general__nassau__precinct.csv"
OE_FILES: dict[str, list[str]] = {
    "2022": [
        "2022/counties/20221108__ny__general__nassau__precinct.csv",
        "2022/counties/20221108__ny__general__suffolk__precinct.csv",
    ],
    "2020": [
        "2020/counties/20201103__ny__general__nassau__precinct.csv",
        "2020/counties/20201103__ny__general__suffolk__precinct.csv",
    ],
}

# Map OpenElections "office" values to our race_type keys
OFFICE_MAP = {
    "State Assembly": "assembly",
    "State Senate":   "senate",
    "U.S. House":     "congressional",
}

# District numbers we care about (confirmed from voter files)
RACE_DISTRICTS = {
    "assembly":      list(range(1, 23)),
    "senate":        [1, 2, 3, 4, 5, 6, 7, 8, 9],
    "congressional": [1, 2, 3, 4],
}


# ── Aggregation ───────────────────────────────────────────────────────────────

def aggregate_contest(rows: list[dict]) -> dict:
    """
    Given all precinct rows for one (office, district) in one county+year,
    compute total votes per candidate (summing across all party lines since NY
    uses fusion voting), identify the Dem and Rep candidates, and return a
    results record.
    """
    # votes per (candidate, party) and total per candidate
    cand_party: dict[tuple, int] = defaultdict(int)
    cand_total: dict[str, int]   = defaultdict(int)

    for row in rows:
        cand  = (row.get("candidate") or "").strip()
        party = (row.get("party")     or "").strip().upper()
        try:
            votes = int(row.get("votes") or 0)
        except ValueError:
            votes = 0
        if not cand:
            continue
        cand_party[(cand, party)] += votes
        cand_total[cand]          += votes

    if not cand_total:
        return {}

    # The Dem candidate = the one with the most votes cast on the DEM party line
    dem_cands = [c for (c, p) in cand_party if p == "DEM"]
    rep_cands = [c for (c, p) in cand_party if p == "REP"]

    dem_cand = max(dem_cands, key=lambda c: cand_party.get((c, "DEM"), 0), default=None)
    rep_cand = max(rep_cands, key=lambda c: cand_party.get((c, "REP"), 0), default=None)

    dem_votes = cand_total.get(dem_cand, 0) if dem_cand else 0
    rep_votes = cand_total.get(rep_cand, 0) if rep_cand else 0
    total_votes = sum(cand_total.values())
    other_votes = max(0, total_votes - dem_votes - rep_votes)

    if total_votes == 0:
        return {}

    dem_pct    = round(dem_votes / total_votes * 100, 1)
    margin_pct = round((dem_votes - rep_votes) / total_votes * 100, 1)
    # True winner = highest vote-getter (may be neither DEM nor REP in an uncontested race)
    top_cand   = max(cand_total, key=cand_total.__getitem__)
    top_party  = max([(p, v) for (c, p), v in cand_party.items() if c == top_cand],
                     key=lambda x: x[1], default=(None, 0))[0]
    if dem_cand and dem_votes > rep_votes:
        winner = "DEM"
    elif rep_cand and rep_votes > dem_votes:
        winner = "REP"
    else:
        winner = top_party or ""

    return {
        "dem_votes":     dem_votes,
        "rep_votes":     rep_votes,
        "other_votes":   other_votes,
        "total_votes":   total_votes,
        "dem_candidate": dem_cand or "",
        "rep_candidate": rep_cand or "",
        "dem_pct":       dem_pct,
        "margin_pct":    margin_pct,
        "winner":        winner,
    }


def parse_csv(text: str) -> dict[str, dict[str, dict[str, list]]]:
    """
    Parse OpenElections CSV into {race_type: {district: [rows]}}.
    """
    result: dict[str, dict[str, list]] = {rt: defaultdict(list) for rt in RACE_DISTRICTS}
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        office   = (row.get("office") or "").strip()
        race_type = OFFICE_MAP.get(office)
        if not race_type:
            continue
        district = (row.get("district") or "").strip()
        if not district or not district.isdigit():
            continue
        if int(district) not in RACE_DISTRICTS[race_type]:
            continue
        result[race_type][district].append(row)
    return result


def fetch_year(year: str) -> dict[str, dict[str, dict]]:
    """Fetch and aggregate all LI results for one election year."""
    files = OE_FILES.get(year, [])
    if not files:
        return {}

    # Accumulate rows per (race_type, district) across counties
    accumulated: dict[str, dict[str, list]] = {rt: defaultdict(list) for rt in RACE_DISTRICTS}

    for path in files:
        url = f"{OE_RAW}/{path}"
        county = "Nassau" if "nassau" in path else "Suffolk"
        print(f"  Fetching {county} {year} from OpenElections...", end=" ", flush=True)
        try:
            r = requests.get(url, timeout=60)
            r.raise_for_status()
            parsed = parse_csv(r.text)
            for race_type, districts in parsed.items():
                for district, rows in districts.items():
                    accumulated[race_type][district].extend(rows)
            print(f"OK ({len(r.content)//1024} KB)")
        except Exception as exc:
            print(f"FAILED: {exc}")
        time.sleep(0.3)

    # Aggregate into per-district records
    results: dict[str, dict[str, dict]] = {}
    for race_type, districts in accumulated.items():
        year_results: dict[str, dict] = {}
        for district, rows in districts.items():
            rec = aggregate_contest(rows)
            if rec:
                year_results[district] = rec
        if year_results:
            results[race_type] = year_results
            print(f"    {race_type}: {len(year_results)} districts")

    return results


# ── Template generation ───────────────────────────────────────────────────────

def empty_record() -> dict:
    return {
        "dem_votes":     0,
        "rep_votes":     0,
        "other_votes":   0,
        "total_votes":   0,
        "dem_candidate": "",
        "rep_candidate": "",
        "dem_pct":       0.0,
        "margin_pct":    0.0,
        "winner":        "",
    }


def make_template() -> dict:
    return {
        rt: {
            yr: {str(d): empty_record() for d in districts}
            for yr in YEARS
        }
        for rt, districts in RACE_DISTRICTS.items()
    }


# ── Validation ────────────────────────────────────────────────────────────────

def validate(data: dict) -> bool:
    missing = []
    for rt, districts in RACE_DISTRICTS.items():
        for yr in YEARS:
            for d in districts:
                rec = data.get(rt, {}).get(yr, {}).get(str(d))
                if not rec or rec.get("total_votes", 0) == 0:
                    missing.append(f"{rt} D{d} {yr}")
    if missing:
        print(f"Missing/empty ({len(missing)}):")
        for m in missing[:30]:
            print(f"  {m}")
        return False
    print(f"All {sum(len(d) for d in RACE_DISTRICTS.values()) * len(YEARS)} records complete.")
    return True


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--validate", action="store_true")
    args = parser.parse_args()

    DATA.mkdir(exist_ok=True)

    if args.validate:
        if not OUTPUT.exists():
            print(f"ERROR: {OUTPUT} not found")
            sys.exit(1)
        ok = validate(json.loads(OUTPUT.read_text()))
        sys.exit(0 if ok else 1)

    # Start from a fresh template
    data = make_template()

    # Fill in 2022 and 2020 from OpenElections
    for year in ["2022", "2020"]:
        print(f"\n[{year}]")
        year_data = fetch_year(year)
        for race_type, districts in year_data.items():
            for district, rec in districts.items():
                data[race_type][year][district] = rec

    # 2024: preserve any existing manual entries, leave rest as empty template
    if OUTPUT.exists():
        existing = json.loads(OUTPUT.read_text())
        for race_type in RACE_DISTRICTS:
            for district in map(str, RACE_DISTRICTS[race_type]):
                existing_rec = existing.get(race_type, {}).get("2024", {}).get(district)
                if existing_rec and existing_rec.get("total_votes", 0) > 0:
                    data[race_type]["2024"][district] = existing_rec

    OUTPUT.write_text(json.dumps(data, indent=2), encoding="utf-8")

    # Print summary
    print(f"\n→ {OUTPUT.relative_to(ROOT)}")
    for race_type in RACE_DISTRICTS:
        for yr in YEARS:
            filled = sum(
                1 for rec in data[race_type][yr].values()
                if rec.get("total_votes", 0) > 0
            )
            total = len(RACE_DISTRICTS[race_type])
            bar = "✓" if filled == total else f"{filled}/{total}"
            print(f"  {yr} {race_type:15s} {bar}")

    filled_2024 = sum(
        1 for rt in RACE_DISTRICTS
        for rec in data[rt]["2024"].values()
        if rec.get("total_votes", 0) > 0
    )
    total_2024 = sum(len(d) for d in RACE_DISTRICTS.values())
    if filled_2024 == total_2024:
        print(f"\n2024 results: all {total_2024} districts filled.")
    else:
        print(f"\n2024 results: {filled_2024}/{total_2024} districts filled.")
        print("  Source: elections.ny.gov/election-results or individual Wikipedia district pages.")
    print("\nRebuild map: python build/build_election_map.py")


if __name__ == "__main__":
    main()
