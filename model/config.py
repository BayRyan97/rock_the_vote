"""Shared paths and constants for the model/ pipeline."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DIST = ROOT / "dist"
# Root for everything PII-bearing. Deliberately outside OneDrive and outside any
# git repo (this data covers ~1.9M real people). Override with RTV_PII_ROOT on a
# machine where the default does not exist; pii_dest() below enforces the rule.
_DEFAULT_PII_ROOT = Path(r"C:\data") if os.name == "nt" else Path.home() / "rtv-data"
PII_ROOT = Path(os.environ.get("RTV_PII_ROOT") or _DEFAULT_PII_ROOT).expanduser()
# Local read-only Parquet snapshot of the Supabase model tables; `etl.py
# --source cache` reads this instead of going over the wire (minutes not hours).
CACHE = PII_ROOT / "rock_the_vote_cache"
SCORES_DIR = PII_ROOT / "rock_the_vote_scores"   # export_scores.py writes here
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

def pii_dest(path, what: str) -> Path:
    """Resolve a PII destination, refusing anything inside the synced tree.

    The previous check was a string test against a hardcoded constant, so it
    could only fail if someone edited this file, and it never inspected where
    writing actually lands. The failure it missed: a drive-absolute Windows
    literal is a single RELATIVE component on POSIX, so off Windows it resolved
    under the cwd — that is, inside the repo, the one place it exists to
    prevent. Resolve first, then check containment, and raise rather than
    assert so it survives python -O.
    """
    p = Path(path).expanduser().resolve()
    if p == ROOT or ROOT in p.parents:
        raise SystemExit(
            f"refusing to write {what} to {p}: that is inside the repo ({ROOT}), "
            f"which is OneDrive-synced and public on GitHub. Set RTV_PII_ROOT to "
            f"a directory outside it.")
    if "onedrive" in str(p).lower():
        raise SystemExit(
            f"refusing to write {what} to {p}: OneDrive-synced. Set RTV_PII_ROOT "
            f"to a directory outside it.")
    return p


SEED = 20260710
REF_DATE = "2026-07-10"          # fixed reference date for donation recency features

# Voting history (features_history.py) ------------------------------------
# Target general election E: hist_* features use ONLY ballots cast strictly
# before the year-E general (years < E, plus the year-E primary, which
# precedes it). y_voted_general_{E} is the Phase-2 turnout label; set E to the
# next general (2026) to score the upcoming election once labels are moot.
TARGET_GENERAL_YEAR = 2024

# Labels ------------------------------------------------------------------
# (The old tier_count>=3 turnout proxy is gone: y_turnout is the real year-E
# outcome, and the proxy agreed with it for only ~3/4 of voters.)
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
