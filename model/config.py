"""Shared paths and constants for the model/ pipeline."""
import functools
import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DIST = ROOT / "dist"
# Root for everything PII-bearing. Deliberately outside OneDrive and outside any
# git repo (this data covers ~1.9M real people). Override with RTV_PII_ROOT on a
# machine where the default does not exist; pii_dest() below enforces the rule.
_DEFAULT_PII_ROOT = Path(r"C:\data") if os.name == "nt" else Path.home() / "rtv-data"
PII_ROOT = Path(os.environ.get("RTV_PII_ROOT") or _DEFAULT_PII_ROOT).expanduser()
BUILD = ROOT / "build"
MODEL = ROOT / "model"


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


# Local read-only Parquet snapshot of the Supabase model tables; `etl.py
# --source cache` reads this instead of going over the wire (minutes not hours).
# Durable copy of the local secrets file. .env.local is gitignored, so it exists
# only inside a working tree: `git clean -x`, or removing a worktree, deletes it
# with no copy in git to restore from. That is precisely how it was lost on
# 2026-08-01, taking DATABASE_URL with it. The master copy therefore lives beside
# the cache -- outside the repo, outside OneDrive, outside git -- and db.py falls
# back to it. The repo-root copy still wins when present, because Next.js reads
# .env.local from the project root and that must keep working.
SECRETS_ENV = PII_ROOT / "rock_the_vote_secrets" / ".env.local"

CACHE = PII_ROOT / "rock_the_vote_cache"
SCORES_DIR = PII_ROOT / "rock_the_vote_scores"   # export_scores.py writes here
# Pipeline outputs live beside the cache, NOT in the repo: persons.parquet alone
# carries names, addresses and party registration for ~1.85M people, and every
# other artifact joins back to it on person_row/person_id. Keeping the rule at
# the directory level means a new artifact is covered automatically, rather than
# depending on someone classifying it correctly. Validated at import, so a
# misconfigured PII root stops every stage rather than failing at first write.
ARTIFACTS = pii_dest(PII_ROOT / "rock_the_vote_artifacts", "model artifacts")

VOTER_SOURCES = [DATA / "Nassau_Unrolled.csv", DATA / "Suffolk_Unrolled.csv"]
NYBOE_B64 = DIST / "nyboe-data.b64"          # base64(gzip(json)): key -> {c: [...], t: total}
COUNTY_B64 = DIST / "nassau-data.b64"        # county payload embedding fec_donations
FEC_CACHE = DATA / "fec_cache.json"          # preferred if present (main checkout only)
NYBOE_CACHE = DATA / "nyboe_cache.json"

MANIFEST = MODEL / "manifest.yaml"
PERSONS_PARQUET = ARTIFACTS / "persons.parquet"
DONOR_COMMITTEES_PARQUET = ARTIFACTS / "donor_committees.parquet"
ELECTIONS_PARQUET = ARTIFACTS / "elections.parquet"        # (person_row, year, etype, method)
HISTORY_FEATURES_PARQUET = ARTIFACTS / "history_features.parquet"   # as-of TARGET
HISTORY_SERVE_PARQUET = ARTIFACTS / "history_features_serve.parquet"  # as-of SERVE
SPLITS_PARQUET = ARTIFACTS / "splits.parquet"
ACS_FEATURES_PARQUET = ARTIFACTS / "acs_features.parquet"
GRAPH_PT = ARTIFACTS / "graph.pt"                 # node features as-of TARGET
GRAPH_SERVE_PT = ARTIFACTS / "graph_serve.pt"     # as-of SERVE, scoring only
BASELINE_METRICS_JSON = ARTIFACTS / "baseline_metrics.json"
GTN_METRICS_JSON = ARTIFACTS / "gtn_metrics.json"
SCORES_PARQUET = ARTIFACTS / "scores.parquet"
TURFS_PARQUET = ARTIFACTS / "turfs.parquet"                     # non-PII, shareable
TURF_ASSIGNMENT_PARQUET = ARTIFACTS / "turf_assignment.parquet"  # joins back to persons
FACILITIES_PARQUET = ARTIFACTS / "facilities.parquet"            # buildings, not doors -- own tactic track


# Manifest ----------------------------------------------------------------
# The manifest lives here rather than in catboost_util because it is
# configuration that every stage reads, including the GTN path, which has no
# business importing CatBoost. That coupling is why graph_build.py used to parse
# the file itself and so escaped the invariant below.

def validate_spec(spec: dict) -> None:
    """The manifest's own invariant, checked wherever the manifest is read.

    spans_cutoff means "summarises history THROUGH the export date", so the
    feature carries the target election's outcome. It may inform the party task,
    never turnout -- and "never turnout" includes the shared GNN encoder,
    because gtn.py feeds the encoder output into the turnout head.

    Checking on read rather than at each consumer is the point:
    catboost_util.assert_no_cutoff_spanning ran only from baseline_catboost and
    score_persons, while graph_build parsed the manifest itself and
    backtest_temporal reads it twice, so retagging one feature aborted CatBoost
    loudly and trained a leaky GTN turnout head in silence.
    """
    leaked = sorted(n for n, m in spec.items() if m.get("spans_cutoff")
                    and {"encoder", "turnout_head"} & set(m.get("usage", [])))
    if leaked:
        raise AssertionError(
            f"manifest.yaml: {leaked} are tagged spans_cutoff but reach the "
            f"turnout task (usage encoder or turnout_head). Those features "
            f"summarise history through the export date, so they contain the "
            f"target election's outcome.")

    # turfs.py (model/turfs/) derives turf_id, m_i and turf value FROM the
    # model's own served scores. Feeding them back in as features is circular
    # -- the model would be predicting a transform of its own output -- and it
    # would look like a large accuracy gain rather than the leakage it is.
    circular = sorted(n for n in spec
                      if n.startswith("turf_") or n.startswith("value_dem_")
                      or n == "m_i")
    if circular:
        raise AssertionError(
            f"manifest.yaml: {circular} look like turfs.py output columns. "
            f"turf id / value / movability mass are derived from this model's "
            f"own scores and must never feed back in as a feature.")


@functools.lru_cache(maxsize=1)
def manifest_spec() -> dict:
    """Feature manifest, validated. Cached: read-only for the life of a run."""
    spec = yaml.safe_load(MANIFEST.read_text())["features"]
    validate_spec(spec)
    return spec


# Graph payload schema. Bump when graph_build.py gains or changes a key that
# train.py/evaluate.py rely on, so a stale graph.pt fails with a message rather
# than a bare KeyError hours into a run. Lives here, not in graph_build, so
# train.py can check it without importing the graph builder's dependencies.
#   2 = adds turnout_fit (the never-voter fit mask)
GRAPH_SCHEMA = 2

SEED = 20260710
REF_DATE = "2026-07-10"          # fixed reference date for donation recency features

# Voting history (features_history.py) ------------------------------------
# Target general election E: hist_* features use ONLY ballots cast strictly
# before the year-E general (years < E, plus the year-E primary, which
# precedes it). y_voted_general_{E} is the Phase-2 turnout label; set E to the
# next general (2026) to score the upcoming election once labels are moot.
TARGET_GENERAL_YEAR = 2024
# The election being PREDICTED, as opposed to the one being learned from.
# Training needs an observed label, so it stays at TARGET_GENERAL_YEAR; serving
# should describe the election the product asks about, using history as-of that
# election. Without this split, a voter whose first ballot was the 2024 general
# is scored on history that predates it — 153,870 of them, at mean 0.115 against
# an observed 0.959. Setting these equal reverts to single-vintage behaviour.
SERVE_GENERAL_YEAR = 2026

# Expected turnout for the election being SERVED, as a rate over the scored
# population. The turnout head is fitted on TARGET_GENERAL_YEAR, and cycle type
# — not recency — sets the LEVEL of general-election turnout: measured on this
# file with the eligibility rule in features_history, presidentials run 0.679
# (2016) / 0.782 (2020) / 0.783 (2024) against midterms at 0.315 (2014) / 0.539
# (2018) / 0.574 (2022). Training on 2024 and serving 2026 unshifted therefore
# overstates turnout by ~21 points, which is the same failure backtest_temporal
# measured in the other direction (2022 -> 2024, ED bias -24.2 pts).
#
# The fix is a single logit-intercept shift (see calibration.py): monotone, so
# ranking and AUC are untouched, and only the level moves. Anchored to 2022, the
# most recent NY gubernatorial midterm — 2026 is the same office cycle. Set this
# to None to serve the training vintage's level unshifted.
#
# Revisit if 2026 looks unlike 2022: midterm turnout here has been rising
# (0.539 -> 0.574), so this anchor is mildly conservative.
SERVE_BASE_RATE = 0.5735

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

# Objective 2: turfs (model/turfs/turfs.py) --------------------------------
# Canvass-turf construction on top of the served scores. No retrain, no graph
# embeddings -- see model/turfs/README.md for why this is split from the
# clustering/DGI half of objective #2.
MOVABLE_LO = 0.20                # applied to POST-SHIFT, served-vintage turnout_prob only
MOVABLE_HI = 0.80
MOVABILITY_EXPONENT = 2          # w_raw(p) = [4p(1-p)]^exponent, then normalised to mean 1
SUPPORT_SIDE = "dem"             # "dem" or "rep" -- which lean column values a supporter
SUPPORT_MIN = 0.55
SPILLOVER_BETA = 0.60            # Nickerson pass-along -- a PRIOR; re-estimate locally before trusting it
# Nickerson's β is measured on TWO-registered-voter households. Applied linearly it
# credited one knock at a 275-target building with 3.22 ballots. Spillover mass is
# therefore capped at this multiple of the door-answerer's own mass: no door is worth
# more than (1 + ratio*β) = 2.8x its single best voter. A(h) is the argmax, so every
# m_j <= m_A and the cap is provably inert for 1- and 2-person households -- the only
# sizes Nickerson actually covers. Backstop, not the primary fix: TURF_FACILITY_MIN_VOTERS
# below pulls whole buildings out of the walk list before this ever applies.
SPILLOVER_MAX_RATIO = 3.0
CANVASS_DIRECT_EFFECT = 0.098    # Nickerson direct effect on the door-answerer -- a PRIOR
CANVASS_CONTACT_RATE = 0.25      # fraction of doors where anyone is reached -- ours, not the literature's
CANVASS_DOORS_PER_HOUR = 20      # for the hours-per-vote report, not the value formula
# Deliberately NOT HOUSEHOLD_CLIQUE_CAP, though it starts at the same value. That one
# governs GNN edge sampling, so changing it invalidates a trained model; this one is an
# operational walk-list decision a field director should be able to retune between
# cycles without forcing a retrain. They are also applied to different denominators.
# An earlier comment here asserted the two were the same cap "applied at the ADDRESS
# level" -- that false equivalence is what hid the fact that the address-level rule
# could never fire (the ETL keys households on household_uuid and already collapses a
# whole building into one row, so distinct-households-per-address is always ~1).
TURF_FACILITY_MIN_VOTERS = 10    # > this many registered voters at one household_row => a building, not a door
TURF_TARGET_DOORS = 150
TURF_MAX_DOORS = 200
TURF_MIN_DOORS = 60
TURF_BREAK_METRES = 400          # gap that force-splits a turf: water crossing, highway, subdivision edge
TURF_MERGE_METRES = 40           # overshoot tolerance so a cul-de-sac isn't split at TURF_TARGET_DOORS
# Ceiling on how far an undersized fragment may reach to find a turf to merge into.
# Measured CENTROID-TO-CENTROID, which is a different scale from TURF_BREAK_METRES
# (that one is between Hilbert-consecutive households, median 30m on this file). Two
# legitimately adjacent full-size turfs sit roughly a turf-diameter apart, so reusing
# the 400m break threshold here rejected nearly every valid merge.
#
# Swept on the real 207,914-household walk track (2026-08-03), reading
# ceiling -> turfs / % under TURF_MIN_DOORS / widest turf:
#     400m ->  4,506 / 72.2% /  3.1km      too strict: 1,563 turfs under 10 doors
#   1,000m ->  2,028 / 23.0% /  3.6km
#   2,000m ->  1,701 /  2.7% /  6.1km   <- knee; fragmentation collapses here
#   3,000m ->  1,673 /  0.8% /  8.9km
#      none ->  1,660 /  0.0% / 25.0km      the old behaviour: a 25km "walkable" turf
# Past 2km the remaining fragments are genuinely isolated -- _merge_undersized_turfs
# documents leaving those alone as correct -- and the only thing more slack buys is
# wider turfs.
TURF_MERGE_MAX_METRES = 2000
CONTROL_FRACTION = 0.08
BUFFER_RING = True                # untreated, unanalysed turfs between arms, to blunt cross-arm spillover
ARM_ASSIGNMENT_SEED = 20261103    # frozen once assigned; changing it invalidates the experiment
