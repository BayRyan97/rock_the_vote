#!/usr/bin/env python3
"""
fetch_acs.py — Enrich households with ACS census tract attributes.

Steps:
  1. Download TIGER 2023 tract shapefile for NY (cached to data/)
  2. Download ACS 2019–2023 5-year estimates for Nassau + Suffolk (cached to data/)
  3. Spatial join: assign each household's lat/lon to a tract
  4. Write ACS attributes back to the households table

Usage:
    CENSUS_API_KEY=<key> python3 build/fetch_acs.py [--dry-run]

Get a free Census API key at: https://api.census.gov/data/key_signup.html

Environment:
    CENSUS_API_KEY  — required
    DATABASE_URL    — Postgres DSN (loaded from .env.local or .env)
"""
import argparse
import io
import json
import os
import zipfile
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras
import requests
from dotenv import load_dotenv
from shapely.geometry import Point

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

load_dotenv(ROOT / ".env.local")
load_dotenv(ROOT / ".env")

DATABASE_URL = os.environ["DATABASE_URL"]
CENSUS_API_KEY = os.environ.get("CENSUS_DATA", os.environ.get("CENSUS_API_KEY", ""))

TIGER_URL = "https://www2.census.gov/geo/tiger/TIGER2023/TRACT/tl_2023_36_tract.zip"
TIGER_CACHE = DATA_DIR / "tiger_tracts_nassau_suffolk.parquet"
ACS_CACHE   = DATA_DIR / "acs_nassau_suffolk.json"

ACS_YEAR = 2023
ACS_VARS = [
    "B19013_001E",   # median household income
    "B15003_001E",   # total pop 25+ (education denominator)
    "B15003_022E",   # bachelor's degree
    "B15003_023E",   # master's degree
    "B15003_024E",   # professional school degree
    "B15003_025E",   # doctorate
    "B25003_001E",   # total occupied housing units (tenure denominator)
    "B25003_002E",   # owner-occupied
    "B03002_001E",   # total race/ethnicity denominator
    "B03002_004E",   # Black or African American alone, non-Hispanic
    "B03002_012E",   # Hispanic or Latino
]

# Nassau = 059, Suffolk = 103
COUNTIES = ["059", "103"]
STATE = "36"


# ── TIGER tract shapefile ──────────────────────────────────────────────────────

def load_tracts() -> gpd.GeoDataFrame:
    if TIGER_CACHE.exists():
        print("Loading cached TIGER tracts …")
        gdf = gpd.read_parquet(TIGER_CACHE)
        return gdf

    print("Downloading TIGER 2023 NY tract shapefile …")
    r = requests.get(TIGER_URL, timeout=120)
    r.raise_for_status()

    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        # extract to a temp dir inside DATA_DIR
        tmp = DATA_DIR / "_tiger_tmp"
        tmp.mkdir(exist_ok=True)
        zf.extractall(tmp)
        shp = next(tmp.glob("*.shp"))
        gdf = gpd.read_file(shp)

    # Filter to Nassau + Suffolk only
    gdf = gdf[gdf["COUNTYFP"].isin(COUNTIES)].copy()
    gdf = gdf.to_crs("EPSG:4326")
    gdf = gdf[["GEOID", "geometry"]].rename(columns={"GEOID": "tract_geoid"})
    gdf.to_parquet(TIGER_CACHE, index=False)
    print(f"  {len(gdf)} tracts cached to {TIGER_CACHE}")
    return gdf


# ── ACS data ──────────────────────────────────────────────────────────────────

def fetch_acs_county(county_fips: str) -> list[dict]:
    url = f"https://api.census.gov/data/{ACS_YEAR}/acs/acs5"
    params = {
        "get": ",".join(ACS_VARS),
        "for": "tract:*",
        "in": f"state:{STATE} county:{county_fips}",
        "key": CENSUS_API_KEY,
    }
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    data = r.json()
    headers = data[0]
    return [dict(zip(headers, row)) for row in data[1:]]


def load_acs() -> pd.DataFrame:
    if ACS_CACHE.exists():
        print("Loading cached ACS data …")
        with open(ACS_CACHE) as f:
            rows = json.load(f)
    else:
        if not CENSUS_API_KEY:
            raise RuntimeError(
                "CENSUS_DATA env var is required. "
                "Get a free key at https://api.census.gov/data/key_signup.html"
            )
        print("Downloading ACS 5-year estimates …")
        rows = []
        for county in COUNTIES:
            rows.extend(fetch_acs_county(county))
            print(f"  Nassau/Suffolk county {county}: {len(rows)} tracts so far")
        with open(ACS_CACHE, "w") as f:
            json.dump(rows, f)
        print(f"  ACS data cached to {ACS_CACHE}")

    df = pd.DataFrame(rows)
    # Build 11-digit GEOID: state (2) + county (3) + tract (6)
    df["tract_geoid"] = df["state"] + df["county"] + df["tract"]

    def to_num(col):
        return pd.to_numeric(df[col], errors="coerce").clip(lower=0)

    df["acs_median_income"] = to_num("B19013_001E").astype("Int64")

    edu_total = to_num("B15003_001E")
    edu_bach  = (to_num("B15003_022E") + to_num("B15003_023E")
                 + to_num("B15003_024E") + to_num("B15003_025E"))
    df["acs_pct_college"] = (edu_bach / edu_total * 100).round(2)

    tenure_total = to_num("B25003_001E")
    tenure_owner = to_num("B25003_002E")
    df["acs_pct_owner_occ"] = (tenure_owner / tenure_total * 100).round(2)

    race_total    = to_num("B03002_001E")
    race_black    = to_num("B03002_004E")
    race_hispanic = to_num("B03002_012E")
    df["acs_pct_black"]    = (race_black    / race_total * 100).round(2)
    df["acs_pct_hispanic"] = (race_hispanic / race_total * 100).round(2)

    keep = ["tract_geoid", "acs_median_income", "acs_pct_college",
            "acs_pct_owner_occ", "acs_pct_black", "acs_pct_hispanic"]
    print(f"  {len(df)} tracts with ACS attributes")
    return df[keep]


# ── Households ────────────────────────────────────────────────────────────────

def load_households(conn) -> pd.DataFrame:
    print("Loading households with lat/lon …")
    sql = "SELECT id, lat::float AS lat, lon::float AS lon FROM households WHERE lat IS NOT NULL AND lon IS NOT NULL"
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql)
        df = pd.DataFrame(cur.fetchall())
    print(f"  {len(df):,} households with coordinates")
    return df


# ── Spatial join ──────────────────────────────────────────────────────────────

def spatial_join(households: pd.DataFrame, tracts: gpd.GeoDataFrame) -> pd.DataFrame:
    print("Running spatial join (point-in-polygon) …")
    geometry = [Point(row.lon, row.lat) for row in households.itertuples()]
    hh_gdf = gpd.GeoDataFrame(households[["id"]], geometry=geometry, crs="EPSG:4326")
    joined = gpd.sjoin(hh_gdf, tracts, how="left", predicate="within")
    # sjoin may produce duplicates on boundary edges — keep first match per household
    joined = joined[~joined.index.duplicated(keep="first")]
    result = households[["id"]].copy()
    result["tract_geoid"] = joined["tract_geoid"].values
    n_matched = result["tract_geoid"].notna().sum()
    print(f"  {n_matched:,}/{len(result):,} households matched to a tract")
    return result


# ── Write-back ────────────────────────────────────────────────────────────────

def write_acs(enriched: pd.DataFrame, conn, dry_run: bool):
    print("Writing ACS attributes to households …")
    sql = """
        UPDATE households SET
            acs_tract_geoid   = %(tract_geoid)s,
            acs_median_income = %(acs_median_income)s,
            acs_pct_college   = %(acs_pct_college)s,
            acs_pct_owner_occ = %(acs_pct_owner_occ)s,
            acs_pct_hispanic  = %(acs_pct_hispanic)s,
            acs_pct_black     = %(acs_pct_black)s
        WHERE id = %(id)s
    """
    records = [
        {
            "id":                str(row.id),
            "tract_geoid":       row.tract_geoid if pd.notna(row.tract_geoid) else None,
            "acs_median_income": int(row.acs_median_income) if pd.notna(row.acs_median_income) else None,
            "acs_pct_college":   float(row.acs_pct_college)   if pd.notna(row.acs_pct_college)   else None,
            "acs_pct_owner_occ": float(row.acs_pct_owner_occ) if pd.notna(row.acs_pct_owner_occ) else None,
            "acs_pct_hispanic":  float(row.acs_pct_hispanic)  if pd.notna(row.acs_pct_hispanic)  else None,
            "acs_pct_black":     float(row.acs_pct_black)     if pd.notna(row.acs_pct_black)     else None,
        }
        for row in enriched.itertuples()
    ]

    if dry_run:
        print(f"  [dry-run] Would update {len(records):,} households — skipping write")
        return

    BATCH = 500
    with conn.cursor() as cur:
        for i in range(0, len(records), BATCH):
            psycopg2.extras.execute_batch(cur, sql, records[i:i + BATCH], page_size=BATCH)
            conn.commit()
            done = min(i + BATCH, len(records))
            print(f"  {done:,}/{len(records):,} updated", flush=True)

    print("Done.")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    tracts = load_tracts()
    acs    = load_acs()
    tracts = tracts.merge(acs, on="tract_geoid", how="left")

    conn = psycopg2.connect(DATABASE_URL)
    try:
        households = load_households(conn)
        matched    = spatial_join(households, tracts)
        enriched   = matched.merge(
            tracts.drop(columns="geometry"), on="tract_geoid", how="left"
        )
        write_acs(enriched, conn, dry_run=args.dry_run)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
