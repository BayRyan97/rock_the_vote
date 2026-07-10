#!/usr/bin/env python3
"""
fetch_district_geo.py — Download NY Assembly (SLDL), Senate (SLDU), and
Congressional district boundaries for Long Island from the Census TIGERweb REST API.

Run once; results are checked into the repo and copied to dist/ by build_election_map.py.

Usage:
    pip install requests
    python build/fetch_district_geo.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).parent.parent
DATA = ROOT / "data"

# District numbers confirmed from Nassau.csv + Suffolk.csv voter files
ASSEMBLY_DISTRICTS  = list(range(1, 23))   # 1–22
SENATE_DISTRICTS    = [1, 2, 3, 4, 5, 6, 7, 8, 9]
CONG_DISTRICTS      = [1, 2, 3, 4]

TIGERWEB_BASE = (
    "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/Legislative/MapServer"
)


def discover_layers() -> dict[str, int]:
    """Query service metadata to find the right layer IDs."""
    url = f"{TIGERWEB_BASE}?f=json"
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    layers = data.get("layers", [])
    layer_map: dict[str, int] = {}
    for layer in layers:
        name = layer.get("name", "").lower()
        lid = layer.get("id")
        if lid is None:
            continue
        if "lower" in name or "assembly" in name or "sldl" in name:
            layer_map["assembly"] = lid
        elif "upper" in name or "senate" in name or "sldu" in name:
            layer_map["senate"] = lid
        elif "congressional" in name or "congress" in name or " cd" in name:
            # Prefer the most recent (highest-numbered) congressional layer
            if "congressional" not in layer_map or lid > layer_map["congressional"]:
                layer_map["congressional"] = lid
    return layer_map


def guess_field_name(layer_id: int, kind: str) -> str:
    """Return the most likely attribute field name for district number."""
    if kind == "assembly":
        return "SLDL"
    if kind == "senate":
        return "SLDU"
    # Congressional — query layer metadata to find the right field
    try:
        meta = requests.get(f"{TIGERWEB_BASE}/{layer_id}?f=json", timeout=15).json()
        for field in meta.get("fields", []):
            name = field.get("name", "")
            if name.startswith("CD") and name not in ("CDSESSN", "CDTYP"):
                return name
    except Exception:
        pass
    return "CD116"  # fallback


def fetch_features(layer_id: int, field: str, values: list[int]) -> list[dict]:
    """Fetch GeoJSON features for the given district numbers."""
    # SLDL/SLDU use 3-digit zero-padding; CD fields use integer comparison
    if field.upper().startswith("CD"):
        where = f"STATE='36' AND CAST({field} AS INTEGER) IN ({','.join(str(v) for v in values)})"
    else:
        value_list = ",".join(f"'{v:03d}'" for v in values)
        where = f"STATE='36' AND {field} IN ({value_list})"
    params = {
        "where": where,
        "outFields": f"{field}",
        "f": "geojson",
        "outSR": "4326",
    }
    url = f"{TIGERWEB_BASE}/{layer_id}/query"
    resp = requests.get(url, params=params, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        return []
    features = data.get("features", [])
    clean = []
    for feat in features:
        props = feat.get("properties") or {}
        raw = props.get(field) or props.get(field.upper())
        if raw is None:
            continue
        try:
            district_num = int(str(raw).lstrip("0") or "0")
        except ValueError:
            continue
        clean.append({
            "type": "Feature",
            "properties": {"district": district_num},
            "geometry": feat["geometry"],
        })
    return clean


def fetch_and_save(kind: str, layer_id: int, districts: list[int], out_path: Path):
    field = guess_field_name(layer_id, kind)
    print(f"  Fetching {kind} districts (layer {layer_id}, field {field})...")
    feats = fetch_features(layer_id, field, districts)

    # Congressional fallback: try alternate field names
    if not feats and kind == "congressional":
        for alt_field in ("CD116", "CD118", "CD116FP", "CDFP", "CD"):
            print(f"    No results — retrying with field {alt_field}...")
            feats = fetch_features(layer_id, alt_field, districts)
            if feats:
                break

    if not feats:
        print(f"  WARNING: no features returned for {kind} — check layer/field names.")
        return

    print(f"    got {len(feats)} features")
    geojson = {"type": "FeatureCollection", "features": feats}
    out_path.write_text(json.dumps(geojson), encoding="utf-8")
    size_kb = out_path.stat().st_size // 1024
    print(f"  → {out_path.relative_to(ROOT)} ({size_kb} KB)")


def main():
    DATA.mkdir(exist_ok=True)

    print("Querying Census TIGERweb Legislative service for layer info...")
    try:
        layer_map = discover_layers()
    except Exception as exc:
        print(f"  WARNING: could not discover layers ({exc}) — using defaults")
        layer_map = {}

    # Fallback defaults based on standard TIGERweb Legislative service structure
    layer_map.setdefault("congressional", 0)
    layer_map.setdefault("senate", 1)
    layer_map.setdefault("assembly", 2)

    print(f"  Using layers: congressional={layer_map['congressional']}, "
          f"senate={layer_map['senate']}, assembly={layer_map['assembly']}")
    print()

    fetch_and_save("assembly", layer_map["assembly"],
                   ASSEMBLY_DISTRICTS, DATA / "li_assembly_districts.geojson")
    fetch_and_save("senate", layer_map["senate"],
                   SENATE_DISTRICTS, DATA / "li_senate_districts.geojson")
    fetch_and_save("congressional", layer_map["congressional"],
                   CONG_DISTRICTS, DATA / "li_congressional_districts.geojson")

    print("\nDone. Now run:")
    print("  python build/fetch_election_results.py   # get or scaffold election results")
    print("  python build/build_election_map.py       # build dist/election_map.html")


if __name__ == "__main__":
    main()
