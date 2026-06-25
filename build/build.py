"""
build.py — regenerates dist/voter_lookup.html from the source data in data/.

Pipeline:
  1. Unzip the TIGER/Line address-range shapefile (if not already extracted)
  2. Parse the voter file (data/Assembly_15_13.xlsx)
  3. Geocode every household against the TIGER street segments
  4. Score every household on the canvass formula (wake-ups + unaffiliated + drop-off Dems)
  5. Dictionary-encode + compress the dataset
  6. Inject it into build/template.html and write dist/voter_lookup.html

Usage:
    pip install -r requirements.txt
    python build.py
"""
import gzip
import base64
import json
import re
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Optional
import math

import pandas as pd
import shapefile  # pyshp

ROOT = Path(__file__).parent.parent
DATA = ROOT / "data"
BUILD = ROOT / "build"
DIST = ROOT / "dist"

VOTER_FILE = DATA / "Assembly_15_13.xlsx"
TIGER_ZIP = DATA / "tl_2025_36059_addrfeat.zip"
TIGER_DIR = BUILD / "tiger_extracted"
TEMPLATE = BUILD / "template.html"
OUTPUT = DIST / "voter_lookup.html"

LOW_TIERS = {"I0", "F1", "L1", "F2", "L2"}
DROPOFF_TIERS = {"I0", "F1", "L1"}

PERSON_PATTERN = re.compile(r"^(.*) \((\d+), ([A-Z]+), ([A-Z0-9]+)\)$")

STREET_SUFFIX_MAP = {
    "AVENUE": "AVE", "STREET": "ST", "ROAD": "RD", "BOULEVARD": "BLVD",
    "DRIVE": "DR", "COURT": "CT", "PLACE": "PL", "LANE": "LN",
    "CIRCLE": "CIR", "PARKWAY": "PKWY", "TURNPIKE": "TPKE",
    "HIGHWAY": "HWY", "TERRACE": "TER", "SQUARE": "SQ", "RIDGE": "RDG",
}


# ---------------------------------------------------------------- geocoding

def normalize_street(name: str) -> str:
    if not name:
        return ""
    name = name.upper().strip()
    for long, short in STREET_SUFFIX_MAP.items():
        name = re.sub(rf"\b{long}\b", short, name)
    return re.sub(r"\s+", " ", name)


def house_number(value) -> Optional[int]:
    if not value:
        return None
    m = re.match(r"^(\d+)", str(value))
    return int(m.group(1)) if m else None


def interpolate(points, frac):
    if len(points) < 2:
        return points[0]
    frac = max(0.0, min(1.0, frac))
    seg_lengths, total = [], 0.0
    for i in range(len(points) - 1):
        dx = points[i + 1][0] - points[i][0]
        dy = points[i + 1][1] - points[i][1]
        length = (dx * dx + dy * dy) ** 0.5
        seg_lengths.append(length)
        total += length
    if total == 0:
        return points[0]
    target = total * frac
    cum = 0.0
    for i, length in enumerate(seg_lengths):
        if cum + length >= target:
            t = (target - cum) / length if length > 0 else 0
            x = points[i][0] + t * (points[i + 1][0] - points[i][0])
            y = points[i][1] + t * (points[i + 1][1] - points[i][1])
            return (x, y)
        cum += length
    return points[-1]


class Geocoder:
    """Builds a street-name -> segment index from the TIGER addrfeat shapefile."""

    def __init__(self, shapefile_base: Path):
        sf = shapefile.Reader(str(shapefile_base))
        self.index: dict[str, list[dict]] = defaultdict(list)
        records, shapes = sf.records(), sf.shapes()
        for rec, shp in zip(records, shapes):
            name = rec["FULLNAME"]
            if not name or len(shp.points) < 2:
                continue
            self.index[normalize_street(name)].append({
                "lfrom": house_number(rec["LFROMHN"]), "lto": house_number(rec["LTOHN"]),
                "rfrom": house_number(rec["RFROMHN"]), "rto": house_number(rec["RTOHN"]),
                "zipl": rec["ZIPL"], "zipr": rec["ZIPR"],
                "pts": shp.points,
            })

    def geocode(self, addr_num, street_name, zip_code):
        n = house_number(addr_num)
        if n is None:
            return None
        norm = normalize_street(street_name)
        segs = self.index.get(norm)
        if not segs:
            tokens = norm.split()
            for end in range(len(tokens) - 1, 0, -1):
                segs = self.index.get(" ".join(tokens[:end]))
                if segs:
                    break
        if not segs:
            return None

        def match_side(seg, side):
            lo, hi = seg[side + "from"], seg[side + "to"]
            if lo is None or hi is None:
                return None
            if not (min(lo, hi) <= n <= max(lo, hi)):
                return None
            z = seg["zipl"] if side == "l" else seg["zipr"]
            zip_matches = (not zip_code) or (not z) or (z == zip_code)
            frac = (n - lo) / (hi - lo) if hi != lo else 0.5
            return frac, zip_matches

        fallback = None
        for seg in segs:
            for side in ("l", "r"):
                result = match_side(seg, side)
                if result is None:
                    continue
                frac, zip_matches = result
                point = interpolate(seg["pts"], frac)
                if zip_matches:
                    return point
                fallback = fallback or point
        return fallback


# ----------------------------------------------------------------- scoring

def parse_household(detail: str):
    if not isinstance(detail, str) or not detail.strip():
        return []
    people = []
    for entry in detail.split(" | "):
        m = PERSON_PATTERN.match(entry.strip())
        if m:
            people.append([m.group(1), int(m.group(2)), m.group(3), m.group(4)])
    return people


def score_household(people):
    """Positives-only canvass score: wake-ups + unaffiliated*2 + drop-off Dems."""
    if not people:
        return 0, 0, 0, 0
    votes = [int(p[3][1:]) if len(p[3]) > 1 and p[3][1:].isdigit() else 0 for p in people]
    gap = max(votes) - min(votes)
    num_low = sum(1 for p in people if p[3] in LOW_TIERS)
    num_blk = sum(1 for p in people if p[2] == "BLK")
    num_dropoff_dem = sum(1 for p in people if p[2] == "DEM" and p[3] in DROPOFF_TIERS)
    wake_ups = gap * num_low
    unaffiliated = num_blk * 2
    dropoff = num_dropoff_dem
    return wake_ups, unaffiliated, dropoff, wake_ups + unaffiliated + dropoff


# ------------------------------------------------------------------- roads

MAJOR_ROAD_MTFCC = {"S1100", "S1200"}


def extract_roads(shapefile_base: Path, bbox):
    lon_min, lon_max, lat_min, lat_max = bbox
    sf = shapefile.Reader(str(shapefile_base))
    name_index: dict[str, int] = {}
    roads = []
    for rec, shp in zip(sf.records(), sf.shapes()):
        if rec["ROAD_MTFCC"] not in MAJOR_ROAD_MTFCC:
            continue
        pts = shp.points
        if len(pts) < 2:
            continue
        if not any(lon_min <= p[0] <= lon_max and lat_min <= p[1] <= lat_max for p in pts):
            continue
        name = rec["FULLNAME"] or ""
        if name not in name_index:
            name_index[name] = len(name_index)
        flat = []
        for lon, lat in pts:
            flat.append(round(lat, 5))
            flat.append(round(lon, 5))
        roads.append([name_index[name], flat])
    names = [None] * len(name_index)
    for name, idx in name_index.items():
        names[idx] = name
    return roads, names


# -------------------------------------------------------------------- main

def main():
    DIST.mkdir(exist_ok=True)

    if not TIGER_DIR.exists():
        print("Extracting TIGER shapefile...")
        TIGER_DIR.mkdir(parents=True)
        with zipfile.ZipFile(TIGER_ZIP) as zf:
            zf.extractall(TIGER_DIR)
    shp_base = next(TIGER_DIR.glob("*.shp")).with_suffix("")

    print("Loading voter file...")
    df = pd.read_excel(VOTER_FILE)
    df["address_number"] = df["address_number"].astype(str)
    df["zip_code"] = df["zip_code"].astype(str)

    print("Building geocoder index...")
    geocoder = Geocoder(shp_base)

    print(f"Geocoding {len(df)} households...")
    lons, lats, misses = [], [], 0
    for _, row in df.iterrows():
        point = geocoder.geocode(row["address_number"], row["street_name"], row["zip_code"])
        if point is None:
            misses += 1
            lons.append(None)
            lats.append(None)
        else:
            lons.append(round(point[0], 5))
            lats.append(round(point[1], 5))
    df["lon"], df["lat"] = lons, lats
    hit_rate = 100 * (len(df) - misses) / len(df)
    print(f"  geocoded {len(df) - misses}/{len(df)} ({hit_rate:.1f}%)")

    print("Scoring households and encoding...")
    street_idx, city_idx, town_idx, party_idx = {}, {}, {}, {}

    def get_idx(table, value):
        if value not in table:
            table[value] = len(table)
        return table[value]

    recs_13, recs_15 = [], []
    for _, row in df.iterrows():
        people = parse_household(row["household_detail"])
        people_enc = []
        for p in people:
            people_enc.append([p[0], p[1], get_idx(party_idx, p[2]), p[3]])
        wake_ups, unaffiliated, dropoff, total = score_household(people)
        
        # Convert NaN to None for valid JSON
        lon = None if (isinstance(row["lon"], float) and math.isnan(row["lon"])) else row["lon"]
        lat = None if (isinstance(row["lat"], float) and math.isnan(row["lat"])) else row["lat"]
        
        record = [
            row["address_number"],
            get_idx(street_idx, row["street_name"]),
            get_idx(city_idx, row["city"]),
            row["zip_code"],
            get_idx(town_idx, row["town"]),
            str(row["election_district"]),
            people_enc,
            lon, lat,
            total, wake_ups, unaffiliated, dropoff,
        ]
        (recs_13 if str(row["assembly_district"]) == "13" else recs_15).append(record)

    print("Extracting major roads for map context...")
    geo_df = df.dropna(subset=["lon", "lat"])
    bbox = (
        geo_df["lon"].min() - 0.01, geo_df["lon"].max() + 0.01,
        geo_df["lat"].min() - 0.01, geo_df["lat"].max() + 0.01,
    )
    roads, road_names = extract_roads(shp_base, bbox)

    cities = geo_df.groupby("city").agg(lat=("lat", "mean"), lon=("lon", "mean"), n=("lat", "count"))
    cities = cities[cities["n"] >= 200].reset_index()
    towns = [[r["city"], round(r["lat"], 5), round(r["lon"], 5), int(r["n"])] for _, r in cities.iterrows()]

    dicts = {
        "streets": [k for k, _ in sorted(street_idx.items(), key=lambda kv: kv[1])],
        "cities": [k for k, _ in sorted(city_idx.items(), key=lambda kv: kv[1])],
        "towns": [k for k, _ in sorted(town_idx.items(), key=lambda kv: kv[1])],
        "parties": [k for k, _ in sorted(party_idx.items(), key=lambda kv: kv[1])],
    }
    payload = {
        "dicts": dicts,
        "13": recs_13,
        "15": recs_15,
        "geo": {"roads": roads, "road_names": road_names, "towns": towns},
    }

    raw = json.dumps(payload, separators=(",", ":"))
    compressed = gzip.compress(raw.encode(), compresslevel=9)
    b64 = base64.b64encode(compressed).decode("ascii")
    print(f"  raw JSON: {len(raw) / 1024 / 1024:.2f} MB -> compressed: {len(b64) / 1024 / 1024:.2f} MB (base64)")

    print("Writing dist/voter_lookup.html...")
    template = TEMPLATE.read_text(encoding="utf-8")
    if "__VOTER_DATA_B64__" not in template:
        raise RuntimeError("template.html is missing the __VOTER_DATA_B64__ placeholder")
    final_html = template.replace("__VOTER_DATA_B64__", b64)
    OUTPUT.write_text(final_html, encoding="utf-8")
    print(f"Done: {OUTPUT} ({len(final_html) / 1024 / 1024:.2f} MB)")


if __name__ == "__main__":
    main()
