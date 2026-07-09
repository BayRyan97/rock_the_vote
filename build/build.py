"""
build.py — regenerates dist/voter_lookup.html from the source data in data/.

Pipeline:
  1. Unzip each county's TIGER/Line address-range shapefile (if not already extracted)
  2. Parse every voter file in VOTER_SOURCES and concatenate them
  3. Geocode every household against its county's TIGER street segments
  4. Score every household on the canvass formula (wake-ups + unaffiliated + drop-off Dems)
  5. Dictionary-encode + compress the dataset, split into one record list per assembly district
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

# Favicon PNGs stored as base64 so build.py is self-contained (no extra asset files).
# Written to dist/ at build time; referenced by path so Safari/iOS can load them.
_FAVICON_32_B64 = "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"
_APPLE_TOUCH_180_B64 = "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"

ROOT = Path(__file__).parent.parent
DATA = ROOT / "data"
BUILD = ROOT / "build"
DIST = ROOT / "dist"

# Every voter file export that should be folded into the build. Each row's own
# "county" column decides which TIGER shapefile geocodes it (see COUNTY_TIGER).
VOTER_SOURCES = [
    DATA / "Nassau.csv",
    DATA / "Suffolk.csv",
]

# FIPS-coded Census TIGER/Line address-range shapefile per county.
COUNTY_TIGER = {
    "NASSAU": DATA / "tl_2025_36059_addrfeat.zip",
    "SUFFOLK": DATA / "tl_2025_36103_addrfeat.zip",
}

TIGER_DIR = BUILD / "tiger_extracted"
TEMPLATE = BUILD / "template.html"
GREEN_TEMPLATE = BUILD / "green_map_template.html"
OUTPUT = DIST / "voter_lookup.html"
GREEN_OUTPUT = DIST / "green_map.html"
ZIP_GEO_SRC = DATA / "nassau_suffolk_zips.geojson"
ZIP_GEO_DEST = DIST / "nassau_suffolk_zips.geojson"

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
    """Builds a street-name -> segment index from a TIGER addrfeat shapefile."""

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
            # zip_matches only when caller gave no zip OR the segment zip matches.
            # An empty segment zip is treated as uncertain (fallback), not a match.
            zip_matches = (not zip_code) or (z == zip_code)
            # zip actively conflicts when segment has an explicit non-empty zip
            # that is different from what the caller gave us.
            zip_conflict = bool(zip_code and z and z != zip_code)
            frac = (n - lo) / (hi - lo) if hi != lo else 0.5
            return frac, zip_matches, zip_conflict

        fallback = None
        zip_conflict_seen = False
        for seg in segs:
            for side in ("l", "r"):
                result = match_side(seg, side)
                if result is None:
                    continue
                frac, zip_matches, zip_conflict = result
                point = interpolate(seg["pts"], frac)
                if zip_matches:
                    return point
                if zip_conflict:
                    zip_conflict_seen = True
                else:
                    fallback = fallback or point
        # If the house number matched segments with explicit wrong zip codes, the
        # street exists in TIGER under a different zip entirely — don't trust a
        # fallback from an unknown-zip segment; it would land in the wrong town.
        return None if zip_conflict_seen else fallback


def extract_tiger(county: str) -> Path:
    """Unzips a county's TIGER shapefile (if needed) and returns its .shp base path."""
    county_dir = TIGER_DIR / county.lower()
    if not county_dir.exists():
        print(f"  extracting TIGER shapefile for {county.title()}...")
        county_dir.mkdir(parents=True)
        with zipfile.ZipFile(COUNTY_TIGER[county]) as zf:
            zf.extractall(county_dir)
    return next(county_dir.glob("*.shp")).with_suffix("")


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


def merge_roads(road_groups):
    """Combines per-county (roads, names) pairs into one set with offset name indices."""
    names: list[str] = []
    roads = []
    for grp_roads, grp_names in road_groups:
        offset = len(names)
        names.extend(grp_names)
        for idx, pts in grp_roads:
            roads.append([idx + offset, pts])
    return roads, names


# ------------------------------------------------------------------- loading

def load_voter_file(path: Path) -> pd.DataFrame:
    print(f"  reading {path.name}...")
    if path.suffix == ".csv":
        df = pd.read_csv(path)
    else:
        df = pd.read_excel(path)
    before = len(df)
    df = df.dropna(subset=["address_number", "street_name"])
    dropped = before - len(df)
    if dropped:
        print(f"    dropped {dropped} rows missing address_number/street_name "
              f"(no usable address, e.g. Fire Island communities without numbered streets)")
    df["address_number"] = df["address_number"].astype(str)
    df["zip_code"] = df["zip_code"].astype(str)
    return df


# -------------------------------------------------------------------- main

def main():
    DIST.mkdir(exist_ok=True)

    print("Loading voter files...")
    df = pd.concat([load_voter_file(p) for p in VOTER_SOURCES], ignore_index=True)
    print(f"  {len(df)} households across {sorted(df['county'].unique())}")

    print("Building geocoder indexes...")
    geocoders = {}
    for county in df["county"].unique():
        shp_base = extract_tiger(county)
        geocoders[county] = Geocoder(shp_base)

    print(f"Geocoding {len(df)} households...")
    lons, lats, misses = [], [], 0
    for _, row in df.iterrows():
        geocoder = geocoders[row["county"]]
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
    voter_party_lookup: dict[str, set] = defaultdict(set)

    def get_idx(table, value):
        if value not in table:
            table[value] = len(table)
        return table[value]

    # Route records to per-county buckets so each county gets its own data file.
    # Cross-county ADs (e.g. AD 9/10/11 on the Nassau/Suffolk border) will have
    # records in BOTH county files; the frontend merges them on load.
    county_records: dict[str, dict[str, list]] = {
        "NASSAU": defaultdict(list),
        "SUFFOLK": defaultdict(list),
    }
    district_county_counts: dict[str, dict] = defaultdict(lambda: defaultdict(int))
    for _, row in df.iterrows():
        people = parse_household(row["household_detail"])
        people_enc = []
        for p in people:
            people_enc.append([p[0], p[1], get_idx(party_idx, p[2]), p[3]])
            fec_key = f"{p[0]}|{str(row['city']).upper().strip()}|{str(row['zip_code']).strip()}"
            voter_party_lookup[fec_key].add(p[2])
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
        ad = str(row["assembly_district"])
        county = row["county"]
        county_records[county][ad].append(record)
        district_county_counts[ad][county] += 1

    print("Extracting major roads for map context...")
    geo_df = df.dropna(subset=["lon", "lat"])
    bbox = (
        geo_df["lon"].min() - 0.01, geo_df["lon"].max() + 0.01,
        geo_df["lat"].min() - 0.01, geo_df["lat"].max() + 0.01,
    )
    road_groups = [extract_roads(extract_tiger(county), bbox) for county in geocoders]
    roads, road_names = merge_roads(road_groups)

    cities = geo_df.groupby("city").agg(lat=("lat", "mean"), lon=("lon", "mean"), n=("lat", "count"))
    cities = cities[cities["n"] >= 200].reset_index()
    towns = [[r["city"], round(r["lat"], 5), round(r["lon"], 5), int(r["n"])] for _, r in cities.iterrows()]

    dicts = {
        "streets": [k for k, _ in sorted(street_idx.items(), key=lambda kv: kv[1])],
        "cities": [k for k, _ in sorted(city_idx.items(), key=lambda kv: kv[1])],
        "towns": [k for k, _ in sorted(town_idx.items(), key=lambda kv: kv[1])],
        "parties": [k for k, _ in sorted(party_idx.items(), key=lambda kv: kv[1])],
    }
    # FEC (federal) donations — embedded in main county payloads (keeps files under 25 MB).
    fec_donations = {}
    FEC_CACHE = DATA / "fec_cache.json"
    if FEC_CACHE.exists():
        print("Loading FEC (federal) donation cache...")
        fec_raw = json.loads(FEC_CACHE.read_text())
        for key, entry in fec_raw.items():
            confirmed = entry.get("confirmed") or []
            if not confirmed:
                continue
            possible = (entry.get("possible") or [])[:5]
            rec: dict = {"c": confirmed, "p": possible}
            parties = voter_party_lookup.get(key, set())
            if len(parties) == 1:
                rec["party"] = next(iter(parties))
            fec_donations[key] = rec
        print(f"  {len(fec_donations)} confirmed federal donors embedded")
    else:
        print("  No FEC cache — run build/fetch_fec_bulk.py")

    # NY BOE (state-level) donations — written as a separate lazy-loaded file
    # so it doesn't push the main county files over Cloudflare's 25 MB limit.
    NYBOE_CACHE = DATA / "nyboe_cache.json"
    NYBOE_OUT   = DIST / "nyboe-data.b64"
    if NYBOE_CACHE.exists():
        print("Loading NY BOE (state) donation cache → nyboe-data.b64...")
        nyboe_raw = json.loads(NYBOE_CACHE.read_text())
        nyboe_donations = {}
        for key, entry in nyboe_raw.items():
            confirmed = (entry.get("confirmed") or [])[:10]
            if not confirmed:
                continue
            nyboe_donations[key] = {"c": confirmed}
        nyboe_bytes = gzip.compress(
            json.dumps(nyboe_donations, separators=(",", ":")).encode(), compresslevel=9
        )
        nyboe_b64 = base64.b64encode(nyboe_bytes).decode()
        NYBOE_OUT.write_text(nyboe_b64)
        size_mb = len(nyboe_b64) // (1024 * 1024)
        print(f"  {len(nyboe_donations)} confirmed state donors → nyboe-data.b64 ({size_mb} MB)")
    else:
        print("  No NY BOE cache — run build/fetch_nyboe.py")

    geo = {"roads": roads, "road_names": road_names, "towns": towns}

    EV_SCORES_FILE = DATA / "ev_zip_scores.json"
    EV_COUNTS_FILE = DATA / "ev_zip_counts.json"
    ev_scores: dict[str, int] = {}
    ev_counts: dict[str, int] = {}
    if EV_SCORES_FILE.exists():
        print("Loading EV zip scores...")
        ev_scores = json.loads(EV_SCORES_FILE.read_text())
        if EV_COUNTS_FILE.exists():
            ev_counts = json.loads(EV_COUNTS_FILE.read_text())
        # Index by both zero-padded ("06390") and stripped ("6390") forms so
        # voter file zips missing leading zeros still get a score.
        for z, s in list(ev_scores.items()):
            stripped = str(int(z))
            if stripped != z:
                ev_scores[stripped] = s
                if z in ev_counts:
                    ev_counts[stripped] = ev_counts[z]
        print(f"  {len(ev_scores)} zip codes with EV scores")
    else:
        print("  No EV scores found — run build/fetch_ev.py to populate")

    def compress_payload(county: str) -> str:
        drecs = county_records[county]
        district_order = sorted(drecs.keys(), key=int)
        payload = {
            "dicts": dicts,
            "district_order": district_order,
            "district_meta": {ad: {"county": county} for ad in district_order},
            "geo": geo,
            "fec_donations": fec_donations,
            "ev_scores": ev_scores,
            "ev_counts": ev_counts,
        }
        for ad, records in drecs.items():
            payload[ad] = records
        raw = json.dumps(payload, separators=(",", ":"))
        compressed = gzip.compress(raw.encode(), compresslevel=9)
        b64 = base64.b64encode(compressed).decode("ascii")
        print(f"  {county}: {len(district_order)} districts, {sum(len(v) for v in drecs.values()):,} households "
              f"— {len(raw)/1024/1024:.1f} MB raw → {len(b64)/1024/1024:.2f} MB b64")
        return b64

    print("Encoding county payloads...")
    nassau_b64 = compress_payload("NASSAU")
    suffolk_b64 = compress_payload("SUFFOLK")

    (DIST / "nassau-data.b64").write_text(nassau_b64, encoding="ascii")
    (DIST / "suffolk-data.b64").write_text(suffolk_b64, encoding="ascii")

    print("Writing dist/voter_lookup.html...")
    template = TEMPLATE.read_text(encoding="utf-8")
    OUTPUT.write_text(template, encoding="utf-8")

    print("Writing dist/green_map.html...")
    if GREEN_TEMPLATE.exists() and ZIP_GEO_SRC.exists():
        # Build a filtered ev_scores/ev_counts dict containing only LI zips
        li_ev_scores = {z: s for z, s in ev_scores.items() if z.startswith("11") or z == "06390"}
        li_ev_counts = {z: c for z, c in ev_counts.items() if z.startswith("11") or z == "06390"}
        green_html = GREEN_TEMPLATE.read_text(encoding="utf-8")
        green_html = green_html.replace(
            "__EV_SCORES__", json.dumps(li_ev_scores, separators=(",", ":"))
        ).replace(
            "__EV_COUNTS__", json.dumps(li_ev_counts, separators=(",", ":"))
        )
        GREEN_OUTPUT.write_text(green_html, encoding="utf-8")
        ZIP_GEO_DEST.write_bytes(ZIP_GEO_SRC.read_bytes())
        green_size = GREEN_OUTPUT.stat().st_size / 1024
        geo_size = ZIP_GEO_DEST.stat().st_size / 1024
        print(f"  green_map.html ({green_size:.0f} KB) + nassau_suffolk_zips.geojson ({geo_size:.0f} KB)")
    else:
        if not GREEN_TEMPLATE.exists():
            print("  Skipping: build/green_map_template.html not found")
        if not ZIP_GEO_SRC.exists():
            print("  Skipping: data/nassau_suffolk_zips.geojson not found — run build/fetch_zip_geo.py")

    # Cloudflare Pages serves files by name with no auto index; without this,
    # "/" has no defined route and can fall back to stale cached responses.
    (DIST / "_redirects").write_text("/ /voter_lookup.html 200\n", encoding="utf-8")

    # Write favicons as real files so Safari can find them (Safari ignores data: URI favicons).
    import base64 as _b64
    (DIST / "favicon.png").write_bytes(_b64.b64decode(_FAVICON_32_B64))
    (DIST / "apple-touch-icon.png").write_bytes(_b64.b64decode(_APPLE_TOUCH_180_B64))

    html_size = OUTPUT.stat().st_size / 1024
    print(f"Done: {OUTPUT} ({html_size:.0f} KB) + nassau-data.b64 + suffolk-data.b64")


if __name__ == "__main__":
    main()
