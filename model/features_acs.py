"""features_acs.py — Stage B: attach ACS block-group demographics to persons.

1. Downloads the NY TIGER block-group shapefile (cached in model/artifacts/).
2. Spatial-joins each household point to its block group (shapely STRtree).
3. Pulls ACS 5-year block-group variables for Nassau (059) + Suffolk (103)
   from the Census API (cached to model/artifacts/acs_raw.json).
4. Adds bg_geoid + acs_* columns to persons.parquet in place.

Usage:
    python model/features_acs.py [--persons PATH]
"""
import argparse
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import shapefile  # pyshp
from shapely.geometry import shape
from shapely import STRtree, points as make_points

import config as C

ACS_YEAR = 2023
TIGER_BG_URL = f"https://www2.census.gov/geo/tiger/TIGER{ACS_YEAR}/BG/tl_{ACS_YEAR}_36_bg.zip"
TIGER_BG_ZIP = C.ARTIFACTS / f"tl_{ACS_YEAR}_36_bg.zip"
COUNTIES = {"NASSAU": "059", "SUFFOLK": "103"}

# The Census Data API requires an API key as of 2025, so we read the keyless
# table-based summary files instead (pipe-delimited, one national file per
# table; block groups carry GEO_ID prefix 1500000US).
ACS_SF_URL = ("https://www2.census.gov/programs-surveys/acs/summary_file/"
              f"{ACS_YEAR}/table-based-SF/data/5YRData/acsdt5y{ACS_YEAR}-{{table}}.dat")
BG_PREFIXES = tuple(f"1500000US36{fips}" for fips in COUNTIES.values())

# table -> {file column -> short name}; derived percentages computed below
ACS_TABLES = {
    "b19013": {"B19013_E001": "median_hh_income"},
    "b01002": {"B01002_E001": "median_age"},
    "b03002": {"B03002_E001": "pop_total", "B03002_E003": "white_nh",
               "B03002_E004": "black_nh", "B03002_E006": "asian_nh",
               "B03002_E012": "hispanic"},
    "b15003": {"B15003_E001": "edu_total_25plus", "B15003_E022": "edu_bachelors",
               "B15003_E023": "edu_masters", "B15003_E024": "edu_professional",
               "B15003_E025": "edu_doctorate"},
    "b25003": {"B25003_E001": "tenure_total", "B25003_E002": "tenure_owner"},
}


def download_bg_shapefile() -> Path:
    if not TIGER_BG_ZIP.exists():
        print(f"Downloading {TIGER_BG_URL}...")
        r = requests.get(TIGER_BG_URL, timeout=120)
        r.raise_for_status()
        TIGER_BG_ZIP.write_bytes(r.content)
    out_dir = C.ARTIFACTS / TIGER_BG_ZIP.stem
    if not out_dir.exists():
        out_dir.mkdir(parents=True)
        with zipfile.ZipFile(TIGER_BG_ZIP) as zf:
            zf.extractall(out_dir)
    return next(out_dir.glob("*.shp")).with_suffix("")


def load_block_groups(shp_base: Path):
    """Return (geoms, geoids, aland_sqkm) filtered to Nassau + Suffolk."""
    sf = shapefile.Reader(str(shp_base))
    geoms, geoids, aland = [], [], []
    wanted = set(COUNTIES.values())
    for rec, shp in zip(sf.records(), sf.shapes()):
        if rec["COUNTYFP"] not in wanted:
            continue
        geoms.append(shape(shp.__geo_interface__))
        geoids.append(rec["GEOID"])
        aland.append(max(rec["ALAND"], 1) / 1e6)
    print(f"  {len(geoms)} block groups in Nassau+Suffolk")
    return geoms, geoids, np.asarray(aland)


def spatial_join(persons: pd.DataFrame, geoms, geoids) -> pd.Series:
    """Map each person to a block-group GEOID via household lat/lon."""
    pts = persons[["lon", "lat"]].drop_duplicates().dropna()
    tree = STRtree(geoms)
    geom_pts = make_points(pts["lon"].to_numpy(), pts["lat"].to_numpy())
    pt_idx, poly_idx = tree.query(geom_pts, predicate="within")
    lookup = pd.Series([geoids[j] for j in poly_idx],
                       index=pd.MultiIndex.from_arrays([
                           pts["lon"].to_numpy()[pt_idx], pts["lat"].to_numpy()[pt_idx]]))
    # some points may sit exactly on a boundary; fall back to nearest polygon
    matched = 100 * len(pt_idx) / max(len(pts), 1)
    print(f"  point-in-polygon matched {len(pt_idx):,}/{len(pts):,} unique points ({matched:.1f}%)")
    keyed = pd.MultiIndex.from_arrays([persons["lon"], persons["lat"]])
    return pd.Series(lookup.reindex(keyed).to_numpy(), index=persons.index, name="bg_geoid")


def fetch_acs() -> pd.DataFrame:
    """Download+filter the per-table summary files to Nassau/Suffolk block groups."""
    acs = None
    for table, colmap in ACS_TABLES.items():
        cache = C.ARTIFACTS / f"acs_{table}_bg.csv"
        if cache.exists():
            df = pd.read_csv(cache, dtype={"bg_geoid": str})
        else:
            url = ACS_SF_URL.format(table=table)
            print(f"  downloading {table}...")
            r = requests.get(url, timeout=600)
            r.raise_for_status()
            lines = r.text.splitlines()
            header = lines[0].split("|")
            rows = [ln.split("|") for ln in lines[1:] if ln.startswith(BG_PREFIXES)]
            df = pd.DataFrame(rows, columns=header)[["GEO_ID", *colmap]]
            df["bg_geoid"] = df["GEO_ID"].str[-12:]
            df = df.drop(columns="GEO_ID")
            df.to_csv(cache, index=False)
        df = df.rename(columns=colmap)
        for col in colmap.values():
            df[col] = pd.to_numeric(df[col], errors="coerce")
            df.loc[df[col] < -100000, col] = np.nan  # Census missing sentinels
        acs = df if acs is None else acs.merge(df, on="bg_geoid", how="outer")
    print(f"  ACS block groups: {len(acs):,}")
    return acs


def derive_features(acs: pd.DataFrame, aland_by_geoid: pd.Series) -> pd.DataFrame:
    out = pd.DataFrame({"bg_geoid": acs["bg_geoid"]})
    pop = acs["pop_total"].replace(0, np.nan)
    edu = acs["edu_total_25plus"].replace(0, np.nan)
    ten = acs["tenure_total"].replace(0, np.nan)
    out["acs_median_hh_income"] = acs["median_hh_income"]
    out["acs_median_age"] = acs["median_age"]
    out["acs_pct_white"] = acs["white_nh"] / pop
    out["acs_pct_black"] = acs["black_nh"] / pop
    out["acs_pct_asian"] = acs["asian_nh"] / pop
    out["acs_pct_hispanic"] = acs["hispanic"] / pop
    out["acs_pct_bachelors"] = (acs[["edu_bachelors", "edu_masters",
                                     "edu_professional", "edu_doctorate"]].sum(axis=1) / edu)
    out["acs_pct_owner_occ"] = acs["tenure_owner"] / ten
    out["acs_pop_density"] = acs["pop_total"] / aland_by_geoid.reindex(acs["bg_geoid"]).to_numpy()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--persons", type=Path, default=C.PERSONS_PARQUET)
    args = ap.parse_args()

    persons = pd.read_parquet(args.persons)
    persons = persons.drop(columns=[c for c in persons.columns
                                    if c.startswith("acs_") or c == "bg_geoid"])
    print("Loading block-group polygons...")
    shp_base = download_bg_shapefile()
    geoms, geoids, aland = load_block_groups(shp_base)
    print("Spatial join...")
    persons["bg_geoid"] = spatial_join(persons, geoms, geoids)
    covered = persons["bg_geoid"].notna().mean()
    geo_covered = persons.loc[persons["has_geo"] == 1, "bg_geoid"].notna().mean()
    print(f"  bg_geoid coverage: {100 * covered:.1f}% of all persons, "
          f"{100 * geo_covered:.1f}% of geocoded persons")

    print("Fetching ACS...")
    acs = fetch_acs()
    aland_by_geoid = pd.Series(aland, index=geoids)
    feats = derive_features(acs, aland_by_geoid)
    persons = persons.merge(feats, on="bg_geoid", how="left")
    acs_cols = [c for c in persons.columns if c.startswith("acs_")]
    print(f"  ACS feature medians:\n{persons[acs_cols].median().round(3).to_string()}")
    persons.to_parquet(args.persons, index=False)
    print(f"Rewrote {args.persons} with {len(acs_cols)} ACS columns")


if __name__ == "__main__":
    main()
