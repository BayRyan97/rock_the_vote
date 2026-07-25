#!/usr/bin/env python3
"""
build_election_map.py — Standalone builder for dist/election_map.html.

Reads Nassau.csv + Suffolk.csv to compute per-district voter-file metrics,
injects those + election_results.json into election_map_template.html,
and writes dist/election_map.html plus the three companion GeoJSON files.

Much faster than running build.py because it skips TIGER geocoding.

Usage:
    python build/build_election_map.py
    python -m http.server 8000 --directory dist   # then open localhost:8000/election_map.html
"""
import json
import re
from collections import defaultdict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent.parent
DATA  = ROOT / "data"
BUILD = ROOT / "build"
DIST  = ROOT / "dist"

VOTER_SOURCES = [
    DATA / "Nassau.csv",
    DATA / "Suffolk.csv",
]

TEMPLATE  = BUILD / "election_map_template.html"
OUTPUT    = DIST  / "election_map.html"
RESULTS_FILE = DATA / "election_results.json"

GEO_FILES = {
    "assembly":     "li_assembly_districts.geojson",
    "senate":       "li_senate_districts.geojson",
    "congressional": "li_congressional_districts.geojson",
}

# Tiers considered "drop-off" (registered Dems who skip non-presidential elections)
DROPOFF_TIERS = {"I0", "F1", "L1"}
LOW_TIERS     = {"I0", "F1", "L1", "F2", "L2"}

# Columns needed from voter file (fast read)
USECOLS = [
    "county", "assembly_district", "senate_district", "congressional_district",
    "voters_at_address", "engagement_gap",
    "num_reliable", "num_low_engagement",
    "household_detail",
]


# ── Voter file aggregation ─────────────────────────────────────────────────────

def compute_district_metrics(df: pd.DataFrame) -> dict[str, dict[str, dict]]:
    """
    Compute per-district voter-file metrics for all three race types.
    Returns: { "assembly": { "12": {...}, ... }, "senate": {...}, "congressional": {...} }
    """
    # Count party registrations via fast string counting on household_detail column
    # Format: "Name (age, PARTY, TIER) | ..."
    hd = df["household_detail"].fillna("")

    df = df.copy()
    df["dem_count"]    = hd.str.count(r", DEM, ")
    df["rep_count"]    = hd.str.count(r", REP, ")
    df["con_count"]    = hd.str.count(r", CON, ")
    df["blk_count"]    = hd.str.count(r", BLK, ")

    # Drop-off Dems: registered Dem + low-engagement tier
    df["dropoff_dem"] = (
        hd.str.count(r", DEM, I0\)") +
        hd.str.count(r", DEM, F1\)") +
        hd.str.count(r", DEM, L1\)")
    )

    district_cols = {
        "assembly":      "assembly_district",
        "senate":        "senate_district",
        "congressional": "congressional_district",
    }

    out: dict[str, dict[str, dict]] = {}

    for race_type, col in district_cols.items():
        if col not in df.columns:
            continue
        sub = df.dropna(subset=[col]).copy()
        sub[col] = sub[col].astype(int).astype(str)

        agg = sub.groupby(col).agg(
            total_households=("voters_at_address", "count"),
            total_voters=("voters_at_address", "sum"),
            dem_count=("dem_count", "sum"),
            rep_count=("rep_count", "sum"),
            con_count=("con_count", "sum"),
            blk_count=("blk_count", "sum"),
            dropoff_dem=("dropoff_dem", "sum"),
            avg_engagement_gap=("engagement_gap", "mean"),
            num_reliable=("num_reliable", "sum"),
            num_low_engagement=("num_low_engagement", "sum"),
        )

        race_metrics: dict[str, dict] = {}
        for district, row in agg.iterrows():
            total_v = int(row["total_voters"])
            dem_n   = int(row["dem_count"])
            rep_n   = int(row["rep_count"])
            con_n   = int(row["con_count"])
            blk_n   = int(row["blk_count"])
            total_reg = dem_n + rep_n + con_n + blk_n or 1  # avoid division by zero

            dem_pct = round(dem_n / total_reg * 100, 1)
            rep_pct = round(rep_n / total_reg * 100, 1)
            blk_pct = round(blk_n / total_reg * 100, 1)

            race_metrics[district] = {
                "total_households": int(row["total_households"]),
                "total_voters":     total_v,
                "dem_pct":          dem_pct,
                "rep_pct":          rep_pct,
                "blk_pct":          blk_pct,
                "registration_gap": round(dem_pct - rep_pct, 1),
                "dropoff_dem_count": int(row["dropoff_dem"]),
                "avg_engagement_gap": round(float(row["avg_engagement_gap"]), 2),
                "reliable_pct":     round(int(row["num_reliable"]) / max(total_v, 1) * 100, 1),
                "low_engage_pct":   round(int(row["num_low_engagement"]) / max(total_v, 1) * 100, 1),
            }

        out[race_type] = race_metrics

    return out


def load_voter_files() -> pd.DataFrame:
    frames = []
    for path in VOTER_SOURCES:
        if not path.exists():
            print(f"  Skipping missing file: {path.name}")
            continue
        available = pd.read_csv(path, nrows=0).columns.tolist()
        cols = [c for c in USECOLS if c in available]
        print(f"  Loading {path.name}...")
        frames.append(pd.read_csv(path, usecols=cols, low_memory=False))
    if not frames:
        raise FileNotFoundError("No voter files found in data/")
    return pd.concat(frames, ignore_index=True)


# ── Build ──────────────────────────────────────────────────────────────────────

def main():
    DIST.mkdir(exist_ok=True)

    # ── Voter file metrics ──────────────────────────────────────────────────
    print("Loading voter files (this may take ~30 seconds)...")
    df = load_voter_files()
    print(f"  {len(df):,} households loaded")

    print("Computing district metrics...")
    district_metrics = compute_district_metrics(df)
    counts = {rt: len(d) for rt, d in district_metrics.items()}
    print(f"  Assembly: {counts.get('assembly', 0)} districts, "
          f"Senate: {counts.get('senate', 0)}, "
          f"Congressional: {counts.get('congressional', 0)}")

    # ── Election results ────────────────────────────────────────────────────
    election_results: dict = {}
    if RESULTS_FILE.exists():
        election_results = json.loads(RESULTS_FILE.read_text())
        total = sum(
            len(districts)
            for race_data in election_results.values()
            for districts in race_data.values()
        )
        print(f"Loaded election results: {total} district-year records")
    else:
        print("No election_results.json found — map will show registration data only.")
        print("  Run: python build/fetch_election_results.py --generate-template")

    # ── Copy GeoJSON companion files ────────────────────────────────────────
    geo_status = {}
    for race_type, fname in GEO_FILES.items():
        src = DATA / fname
        dst = DIST / fname
        if src.exists():
            dst.write_bytes(src.read_bytes())
            size_kb = dst.stat().st_size // 1024
            geo_status[race_type] = f"{fname} ({size_kb} KB)"
        else:
            geo_status[race_type] = None
            print(f"  WARNING: {fname} missing — run build/fetch_district_geo.py")

    print("GeoJSON files copied:")
    for rt, status in geo_status.items():
        print(f"  {rt}: {status or 'MISSING'}")

    # ── Build HTML ──────────────────────────────────────────────────────────
    if not TEMPLATE.exists():
        print(f"ERROR: {TEMPLATE} not found")
        return

    print("Building dist/election_map.html...")
    html = TEMPLATE.read_text(encoding="utf-8")
    html = html.replace(
        "__ELECTION_RESULTS__",
        json.dumps(election_results, separators=(",", ":"))
    ).replace(
        "__DISTRICT_METRICS__",
        json.dumps(district_metrics, separators=(",", ":"))
    ).replace(
        "__GEO_STATUS__",
        json.dumps({rt: bool(v) for rt, v in geo_status.items()})
    )
    OUTPUT.write_text(html, encoding="utf-8")
    size_kb = OUTPUT.stat().st_size // 1024
    print(f"  → dist/election_map.html ({size_kb} KB)")

    print("\nDone! To view locally:")
    print("  python -m http.server 8000 --directory dist")
    print("  open http://localhost:8000/election_map.html")


if __name__ == "__main__":
    main()
