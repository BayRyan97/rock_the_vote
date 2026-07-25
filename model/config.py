"""Shared paths and constants for the model/ pipeline."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DIST = ROOT / "dist"
BUILD = ROOT / "build"
MODEL = ROOT / "model"
ARTIFACTS = MODEL / "artifacts"

VOTER_SOURCES = [DATA / "Nassau_Unrolled.csv", DATA / "Suffolk_Unrolled.csv"]
NYBOE_B64 = DIST / "nyboe-data.b64"          # base64(gzip(json)): key -> {c: [...], t: total}
COUNTY_B64 = DIST / "nassau-data.b64"        # county payload embedding fec_donations
FEC_CACHE = DATA / "fec_cache.json"          # preferred if present (main checkout only)
NYBOE_CACHE = DATA / "nyboe_cache.json"

MANIFEST = MODEL / "manifest.yaml"
PERSONS_PARQUET = ARTIFACTS / "persons.parquet"
DONOR_COMMITTEES_PARQUET = ARTIFACTS / "donor_committees.parquet"
ELECTIONS_PARQUET = ARTIFACTS / "elections.parquet"        # (person_row, year, etype, method)
HISTORY_FEATURES_PARQUET = ARTIFACTS / "history_features.parquet"
SPLITS_PARQUET = ARTIFACTS / "splits.parquet"
ACS_FEATURES_PARQUET = ARTIFACTS / "acs_features.parquet"
GRAPH_PT = ARTIFACTS / "graph.pt"
BASELINE_METRICS_JSON = ARTIFACTS / "baseline_metrics.json"
GTN_METRICS_JSON = ARTIFACTS / "gtn_metrics.json"
SCORES_PARQUET = ARTIFACTS / "scores.parquet"

SEED = 20260710
REF_DATE = "2026-07-10"          # fixed reference date for donation recency features

# Voting history (features_history.py) ------------------------------------
# Target general election E: hist_* features use ONLY ballots cast strictly
# before the year-E general (years < E, plus the year-E primary, which
# precedes it). y_voted_general_{E} is the Phase-2 turnout label; set E to the
# next general (2026) to score the upcoming election once labels are moot.
TARGET_GENERAL_YEAR = 2024

# Labels ------------------------------------------------------------------
TURNOUT_COUNT_THRESHOLD = 3      # y_turnout = tier_count >= 3 ("active voter")
# 3-class party target folding NY fusion parties (research doc §3).
PARTY_CLASS = {"DEM": 0, "WOR": 0, "REP": 1, "CON": 1}
PARTY_MASKED = -1                # BLK / unaffiliated: masked in training, scored at inference
PARTY_OTHER = 2                  # registered minor parties (OTH, IND, ...)
PARTY_CLASS_NAMES = ["dem_lean", "rep_lean", "other_minor"]

# Donation conduits: excluded from co-donor edges (they connect unrelated people),
# folded into per-node features instead.
DEM_CONDUITS = {"ACTBLUE"}
REP_CONDUITS = {"WINRED"}
MAX_COMMITTEE_DONORS_FOR_EDGES = 5000

# Graph -------------------------------------------------------------------
# Edge budget sized for 15.6 GB RAM: ~2M nodes, target < 50M directed edges.
SPATIAL_KNN_K = 10               # nearest OTHER households; one person sampled per neighbor household
ED_PEERS_PER_VOTER = 5
CODONORS_PER_COMMITTEE = 5
SAME_ADDRESS_PEERS = 5
HOUSEHOLD_CLIQUE_CAP = 10        # bigger "households" are facilities; sample peers instead
EDGE_TYPES = ["household", "same_address", "ed", "spatial_knn", "donation"]
CLUSTER_TARGET_PERSONS = 8000    # geographic training clusters (ClusterGCN-style)
RWSE_K = 16                      # random-walk steps for positional encoding

# Splits ------------------------------------------------------------------
SPLIT_FRACS = {"train": 0.8, "val": 0.1, "test": 0.1}
