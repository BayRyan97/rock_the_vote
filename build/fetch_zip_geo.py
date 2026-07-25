#!/usr/bin/env python3
"""
fetch_zip_geo.py — Download ZCTA (zip code tabulation area) boundaries for all
Nassau+Suffolk zip codes from the Census TIGERweb REST API and save as GeoJSON.

Source: Census TIGERweb PUMA_TAD_TAZ_UGA_ZCTA MapServer (2020 ZCTAs)
Input:  data/ev_zip_scores.json   →  which zip codes we care about
Output: data/nassau_suffolk_zips.geojson

Run once; result is checked in to the repo.

Usage:
    pip install requests
    python build/fetch_zip_geo.py
"""
import json
from pathlib import Path

import requests

ROOT = Path(__file__).parent.parent
DATA = ROOT / "data"
SCORES_FILE = DATA / "ev_zip_scores.json"
OUTPUT = DATA / "nassau_suffolk_zips.geojson"

# TIGERweb REST API — 2020 Census ZIP Code Tabulation Areas (layer 1)
# Field name is ZCTA5 (not GEOID20 which belongs to a different layer).
TIGERWEB_URL = (
    "https://tigerweb.geo.census.gov/arcgis/rest/services/"
    "TIGERweb/PUMA_TAD_TAZ_UGA_ZCTA/MapServer/1/query"
)
BATCH = 80  # keep URL short; the IN() clause gets long fast


def fetch_batch(zips: list[str]) -> list[dict]:
    zip_list = ",".join(f"'{z}'" for z in zips)
    params = {
        "where": f"ZCTA5 IN ({zip_list})",
        "outFields": "ZCTA5",
        "f": "geojson",
        "outSR": "4326",
    }
    resp = requests.get(TIGERWEB_URL, params=params, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    features = data.get("features", [])
    # Normalize: keep only zip in properties, drop the rest
    clean = []
    for feat in features:
        props = feat.get("properties") or {}
        geoid = props.get("ZCTA5") or ""
        if geoid:
            clean.append({
                "type": "Feature",
                "properties": {"zip": geoid},
                "geometry": feat["geometry"],
            })
    return clean


def main():
    if not SCORES_FILE.exists():
        print(f"ERROR: {SCORES_FILE} not found — run build/fetch_ev.py first.")
        return

    zips = sorted(json.loads(SCORES_FILE.read_text()).keys())
    # Only keep plausible Long Island zips (11xxx + one edge case 06390 Fishers Island)
    li_zips = [z for z in zips if z.startswith("11") or z == "06390"]
    print(f"Fetching boundaries for {len(li_zips)} Nassau+Suffolk zip codes...")

    all_features: list[dict] = []
    batches = [li_zips[i:i + BATCH] for i in range(0, len(li_zips), BATCH)]
    for i, batch in enumerate(batches, 1):
        print(f"  batch {i}/{len(batches)} ({len(batch)} zips)...")
        feats = fetch_batch(batch)
        all_features.extend(feats)
        print(f"    got {len(feats)} features (total so far: {len(all_features)})")

    geojson = {
        "type": "FeatureCollection",
        "features": all_features,
    }
    OUTPUT.write_text(json.dumps(geojson), encoding="utf-8")
    print(f"\nDone: {len(all_features)} zip boundaries → {OUTPUT.relative_to(ROOT)}")
    missing = set(li_zips) - {f["properties"]["zip"] for f in all_features}
    if missing:
        print(f"Warning: {len(missing)} zips had no boundary data: {sorted(missing)[:10]}")


if __name__ == "__main__":
    main()
