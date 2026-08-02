#!/usr/bin/env python3
"""turfs.py — Phase 1 of objective #2: canvass turfs, ranked by expected
additional Democratic ballots. No retrain, no graph embeddings.

Consumes the served CatBoost/GTN scores the same way score_voters.py and
export_scores.py do; produces nothing that feeds back into either model. See
model/turfs/README.md for the household-vs-address distinction this module
depends on and why it is NOT the DGI/HDBSCAN clustering half of objective #2.

Two outputs, both fingerprint-stamped against the persons table they were
built from (persons_io.write_stamped), both under config.ARTIFACTS:

  turfs.parquet             one row per turf — non-PII, shareable
  turf_assignment.parquet   one row per target voter — joins back to persons_io,
                             not shareable

Usage:
    python model/turfs/turfs.py                    # --model catboost (default)
    python model/turfs/turfs.py --model gtn
    python model/turfs/turfs.py --persons PATH --serve-history PATH  # smoke subset
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config as C  # noqa: E402
from calibration import to_base_rate  # noqa: E402
# catboost_util pulls in catboost + sklearn; persons_io stays numpy/pandas/pyarrow
# only. Both are used exclusively inside load_targets(), so the import is lazy
# there -- everything else in this module (households, hilbert, turfs,
# valuation, arms) is testable with synthetic frames and no ML dependencies.
from persons_io import population_fingerprint, write_stamped  # noqa: E402

SERVE_BASE_RATE_TOL = 0.01


# ------------------------------------------------------------ target pool

def assert_serve_base_rate(turnout_prob: np.ndarray, tol: float = SERVE_BASE_RATE_TOL) -> None:
    """Refuse pre-shift or training-vintage scores (§2.2 of the objective-2 plan).

    The base-rate shift is monotone, so it cannot reorder two voters -- but a
    threshold like MOVABLE_HI is not rank-invariant. Scored on the wrong scale,
    a voter who is genuinely in the movable band reads as "already banked" and
    silently drops out of the pool. The failure has no error message of its
    own: the pipeline runs, the turf list looks plausible, and it has deleted
    a large slice of the most valuable voters in the file. This is the guard
    against that, checked once at the door.
    """
    mean_p = float(np.mean(turnout_prob))
    if abs(mean_p - C.SERVE_BASE_RATE) > tol:
        raise ValueError(
            f"mean served turnout_prob is {mean_p:.4f}, not within {tol} of "
            f"config.SERVE_BASE_RATE={C.SERVE_BASE_RATE}. turfs.py requires "
            f"POST-SHIFT, served-vintage scores. Rerun with --serve-history "
            f"pointed at the as-of-{C.SERVE_GENERAL_YEAR} vintage and the "
            f"default base rate (score_voters.py / export_scores.py apply "
            f"this shift automatically; --base-rate 0 or the training vintage "
            f"will trip this guard on purpose).")


def movability_weight(p_turnout: np.ndarray,
                      exponent: int = C.MOVABILITY_EXPONENT) -> np.ndarray:
    """w(p), normalised to mean 1 OVER THE POOL PASSED IN (§2.4).

    Capping w at 1.0 (its un-normalised max) would silently discount every
    value estimate a second time on top of Δ, which is already an averaged
    effect size — understating value by roughly the same factor the cap
    removes headroom. Normalising to mean 1 makes w redistribute a fixed
    average effect across the propensity distribution rather than shrink it,
    which is what makes the mean-1 and raw-Nickerson computations in
    value_household agree to float precision (see test_value_units).
    """
    p = np.asarray(p_turnout, dtype=np.float64)
    w_raw = (4.0 * p * (1.0 - p)) ** exponent
    mean = w_raw.mean()
    if mean <= 0:
        raise ValueError("movability_weight: pool has zero mean weight — "
                         "every voter is at a turnout extreme")
    return w_raw / mean


def load_targets(*, model: str = "catboost",
                 persons_path: Path = C.PERSONS_PARQUET,
                 acs_path: Path = C.ACS_FEATURES_PARQUET,
                 serve_history: Path = C.HISTORY_SERVE_PARQUET,
                 train_history: Path = C.HISTORY_FEATURES_PARQUET,
                 support_side: str = C.SUPPORT_SIDE,
                 movable_lo: float = C.MOVABLE_LO,
                 movable_hi: float = C.MOVABLE_HI,
                 support_min: float = C.SUPPORT_MIN) -> pd.DataFrame:
    """Join served scores to persons; filter to the movable, supporting pool.

    Scores itself via catboost_util.score_persons (the same function
    score_voters.py and export_scores.py go through) rather than trusting a
    prior export's column names or vintage -- so the SERVE_BASE_RATE guard
    below is checking scores this function itself produced, not a file that
    could have been generated any number of ways.

    Returns one row per TARGET voter (post-filter): person_row, household_row,
    lat, lon, has_geo, ed_key, county, p_turnout, p_support, m_i (§2.4's
    movability-weighted mass, normalised to mean 1 over exactly this pool).
    """
    if support_side not in ("dem", "rep"):
        raise ValueError(f"support_side must be 'dem' or 'rep', got {support_side!r}")

    from catboost_util import score_persons
    from persons_io import load_gtn_scores, load_persons

    persons = load_persons(persons_path, acs_path=acs_path, history_path=serve_history)

    if model == "catboost":
        if Path(serve_history) != Path(train_history):
            party_persons = load_persons(persons_path, acs_path=acs_path,
                                         history_path=train_history)
        else:
            party_persons = persons
        s = score_persons(persons, party_persons, quiet=True)
        turnout_raw = s["turnout"].to_numpy()
        support_raw = (s["dem_lean"] if support_side == "dem" else s["rep_lean"]).to_numpy()
    elif model == "gtn":
        gtn = load_gtn_scores(persons)
        turnout_raw = gtn["turnout_propensity"].to_numpy(np.float32)
        support_raw = (gtn["p_dem_lean"] if support_side == "dem"
                      else gtn["p_rep_lean"]).to_numpy(np.float32)
    else:
        raise ValueError(f"model must be 'catboost' or 'gtn', got {model!r}")

    p_turnout = to_base_rate(turnout_raw, C.SERVE_BASE_RATE, "turnout")
    assert_serve_base_rate(p_turnout)

    pool = pd.DataFrame({
        "person_row": np.arange(len(persons), dtype=np.int64),
        "household_row": persons["household_row"].to_numpy(),
        "county": persons["county"].to_numpy(),
        "city": persons["city"].to_numpy(),
        "street_name": persons["street_name"].to_numpy(),
        "address_number": persons["address_number"].to_numpy(),
        "ed_key": persons["ed_key"].to_numpy(),
        "lat": persons["lat"].to_numpy(),
        "lon": persons["lon"].to_numpy(),
        "has_geo": persons["has_geo"].to_numpy(),
        "p_turnout": p_turnout,
        "p_support": support_raw,
    })

    mask = ((pool["p_turnout"] >= movable_lo) & (pool["p_turnout"] <= movable_hi)
           & (pool["p_support"] >= support_min))
    pool = pool.loc[mask].reset_index(drop=True)
    if pool.empty:
        raise ValueError("no voters survive the movable/support filter — check "
                         "movable_lo/hi and support_min against the served scores")

    pool["m_i"] = movability_weight(pool["p_turnout"].to_numpy()) * pool["p_support"].to_numpy()
    return pool


# --------------------------------------------------------- households/addr

def build_households(targets: pd.DataFrame, *,
                     household_max_size: int = C.HOUSEHOLD_CLIQUE_CAP) -> pd.DataFrame:
    """One row per household THAT CONTAINS A TARGET VOTER.

    hh_id is persons.parquet's own `household_row` -- the real per-record
    household from the ETL, not something reconstructed. addr_id is a
    building-level grouping (county|city|street_name|address_number), the
    same string graph_build.py's same_address_edges groups on. The two are
    genuinely different relations: a six-unit building is six households and
    one address. Unioning them (the tempting one-liner) makes every
    multi-unit building a single facility-sized "household", trips
    is_facility, and silently zeroes spillover for the ordinary households
    inside it -- apartment-dense turfs would rank last for no visible reason.
    test_apartment_building_is_not_one_household pins this.

    is_facility is therefore computed on addr_id and used ONLY for
    walkability (a 200-unit tower is one stop, many doors); household β is
    never touched by it.
    """
    addr = (targets["county"].astype(str) + "|" + targets["city"].astype(str) + "|"
           + targets["street_name"].astype(str) + "|" + targets["address_number"].astype(str))
    t = targets.assign(addr=addr)

    hh = t.groupby("household_row", sort=False).agg(
        lat=("lat", "mean"), lon=("lon", "mean"), has_geo=("has_geo", "max"),
        ed_key=("ed_key", "first"), county=("county", "first"),
        addr=("addr", "first"), n_targets=("household_row", "size"),
    ).reset_index().rename(columns={"household_row": "hh_id"})

    addr_size = t.drop_duplicates("household_row").groupby("addr")["household_row"].transform("size")
    addr_lookup = (t.drop_duplicates("household_row")
                   .assign(addr_size=addr_size)[["household_row", "addr", "addr_size"]]
                   .rename(columns={"household_row": "hh_id"}))
    hh = hh.merge(addr_lookup[["hh_id", "addr_size"]], on="hh_id", how="left")
    hh["addr_id"] = pd.factorize(hh["addr"])[0]
    hh["is_facility"] = hh["addr_size"] > household_max_size
    return hh.drop(columns=["addr", "addr_size"])


# -------------------------------------------------------------- geometry

def planar_metres(lat: np.ndarray, lon: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Local equirectangular projection to metres, centred on the point set.

    Same formula graph_build.py's spatial_knn_edges uses. Fine at Long Island
    scale; would need a real projection for anything continent-sized.
    """
    lat0 = np.nanmean(lat)
    x = lon * 111_320.0 * np.cos(np.radians(lat0))
    y = lat * 110_540.0
    return x, y


def _hilbert_xy2d(x: np.ndarray, y: np.ndarray, order: int) -> np.ndarray:
    """Vectorized Hilbert-curve distance for integer grid coordinates in [0, 2^order).

    Standard iterative xy2d (Wikipedia "Hilbert curve"), run once per bit
    instead of once per point: order=16 is 16 numpy passes over the whole
    array rather than a Python loop per point.
    """
    n = 1 << order
    x = x.astype(np.int64).copy()
    y = y.astype(np.int64).copy()
    d = np.zeros(x.shape, dtype=np.int64)
    s = n >> 1
    while s > 0:
        rx = ((x & s) > 0).astype(np.int64)
        ry = ((y & s) > 0).astype(np.int64)
        d += s * s * ((3 * rx) ^ ry)
        flip = (ry == 0) & (rx == 1)
        xf = np.where(flip, (n - 1 - x), x)
        yf = np.where(flip, (n - 1 - y), y)
        swap = ry == 0
        x, y = np.where(swap, yf, xf), np.where(swap, xf, yf)
        s >>= 1
    return d


def hilbert_index(x_m: np.ndarray, y_m: np.ndarray, order: int = 16) -> np.ndarray:
    """Hilbert index of planar-metre coordinates, quantized to a 2^order grid.

    Hilbert over snake/row-major ordering because it produces compact blobs
    rather than long thin strips -- compactness is the thing being optimised
    (test_hilbert_is_locality_preserving pins the comparison).
    """
    x_m = np.asarray(x_m, dtype=np.float64)
    y_m = np.asarray(y_m, dtype=np.float64)
    n = 1 << order
    x_range = max(x_m.max() - x_m.min(), 1e-9)
    y_range = max(y_m.max() - y_m.min(), 1e-9)
    xi = np.clip(((x_m - x_m.min()) / x_range * (n - 1)).astype(np.int64), 0, n - 1)
    yi = np.clip(((y_m - y_m.min()) / y_range * (n - 1)).astype(np.int64), 0, n - 1)
    return _hilbert_xy2d(xi, yi, order)


# ----------------------------------------------------------- turf building

def build_turfs(households: pd.DataFrame, *,
                target_doors: int = C.TURF_TARGET_DOORS,
                max_doors: int = C.TURF_MAX_DOORS,
                min_doors: int = C.TURF_MIN_DOORS,
                break_metres: float = C.TURF_BREAK_METRES,
                merge_metres: float = C.TURF_MERGE_METRES,
                order: int = 16) -> pd.DataFrame:
    """Hilbert-sort households (that have geocodes), greedily chunk into turfs,
    then merge any turf left under min_doors into its nearest neighbour turf.

    Ungeocoded households (has_geo == 0) get turf_id -1 -- they cannot be
    walked from a Hilbert-ordered list and must be handled separately
    (pre-flight item 4), not silently dropped or piled onto a shared centroid.

    Returns `households` with a `turf_id` column added.
    """
    if households["hh_id"].duplicated().any():
        raise ValueError("build_turfs: households must be one row per hh_id")

    geo = households[households["has_geo"] == 1].copy()
    ungeo = households[households["has_geo"] != 1].copy()

    if geo.empty:
        out = ungeo.copy()
        out["turf_id"] = -1
        return out

    x, y = planar_metres(geo["lat"].to_numpy(), geo["lon"].to_numpy())
    geo["_x"], geo["_y"] = x, y
    geo["_hilbert"] = hilbert_index(x, y, order)
    geo = geo.sort_values("_hilbert").reset_index(drop=True)

    xs, ys = geo["_x"].to_numpy(), geo["_y"].to_numpy()
    n = len(geo)
    turf_of = np.full(n, -1, dtype=np.int64)
    turf_id = 0
    start = 0
    i = 0
    while i < n:
        # forced break: the step from the previous household exceeds break_metres
        if i > start and np.hypot(xs[i] - xs[i - 1], ys[i] - ys[i - 1]) > break_metres:
            for j in range(start, i):
                turf_of[j] = turf_id
            turf_id += 1
            start = i
        size = i - start + 1
        if size >= target_doors:
            at_end = i + 1 >= n
            gap = 0.0 if at_end else np.hypot(xs[i + 1] - xs[i], ys[i + 1] - ys[i])
            if at_end or size >= max_doors or gap > merge_metres:
                for j in range(start, i + 1):
                    turf_of[j] = turf_id
                turf_id += 1
                start = i + 1
        i += 1
    if start < n:
        for j in range(start, n):
            turf_of[j] = turf_id
        turf_id += 1
    geo["turf_id"] = turf_of

    geo = _merge_undersized_turfs(geo, min_doors=min_doors, max_doors=max_doors)

    ungeo["turf_id"] = -1
    out = pd.concat([geo.drop(columns=["_x", "_y", "_hilbert"]), ungeo],
                    ignore_index=True, sort=False)
    return out


def _merge_undersized_turfs(geo: pd.DataFrame, *, min_doors: int, max_doors: int) -> pd.DataFrame:
    """Sweep every turf below min_doors (not just the trailing one) into its
    nearest-centroid neighbour with spare capacity, ascending by size so the
    smallest fragments merge first.

    Centroids are maintained in a dict (recentre by size-weighted average on
    each merge) rather than recomputed by rescanning `geo` per candidate: at
    a few hundred households and a handful of turfs the rescan is invisible,
    but at real scale (hundreds of thousands of households, thousands of
    turfs) recomputing a centroid via a fresh boolean mask over the WHOLE
    table, for every candidate, for every undersized turf, is O(n * k) or
    worse and turns a sub-minute stage into something that never finishes.

    A fragment with no neighbour under max_doors stays undersized -- this is
    a real possibility at the edge of the county, not a bug, and callers that
    care (§8 item 9 of the objective-2 plan) should check the output.
    """
    geo = geo.copy()
    counts = geo["turf_id"].value_counts()
    cent = geo.groupby("turf_id", sort=False)[["_x", "_y"]].mean()
    cx = cent["_x"].to_dict()
    cy = cent["_y"].to_dict()

    small = counts[counts < min_doors].sort_values().index.tolist()
    for tid in small:
        if tid not in counts.index or counts[tid] >= min_doors:
            continue  # already absorbed by an earlier merge in this loop
        candidates = counts[(counts.index != tid) & (counts + counts[tid] <= max_doors)]
        if candidates.empty:
            continue
        cand_ids = candidates.index.to_numpy()
        cxs = np.fromiter((cx[t] for t in cand_ids), dtype=np.float64, count=len(cand_ids))
        cys = np.fromiter((cy[t] for t in cand_ids), dtype=np.float64, count=len(cand_ids))
        d = np.hypot(cxs - cx[tid], cys - cy[tid])
        target = cand_ids[int(np.argmin(d))]

        n_tid, n_target = counts[tid], counts[target]
        n_new = n_tid + n_target
        cx[target] = (cx[target] * n_target + cx[tid] * n_tid) / n_new
        cy[target] = (cy[target] * n_target + cy[tid] * n_tid) / n_new
        del cx[tid], cy[tid]

        # Live reassignment (not a snapshot), so a later merge that folds
        # `target` into something else carries these rows along with it --
        # matches every id currently tagged tid, however it got that tag.
        geo.loc[geo["turf_id"] == tid, "turf_id"] = target
        counts[target] = n_new
        counts = counts.drop(tid)
    # renumber to a dense 0..k-1 range
    geo["turf_id"] = pd.factorize(geo["turf_id"])[0]
    return geo


# ------------------------------------------------------------- valuation

def value_households(households_with_targets: pd.DataFrame, targets: pd.DataFrame, *,
                     beta: float = C.SPILLOVER_BETA,
                     delta: float = C.CANVASS_DIRECT_EFFECT,
                     kappa: float = C.CANVASS_CONTACT_RATE) -> pd.DataFrame:
    """Per-household expected additional Dem (or Rep) ballots (§3.1).

    A(h) = the household member with the largest m_i -- the one worth asking
    for. Everyone else in the household gets the spillover discount β.
    Self-excluded by construction: a 1-person household has no j != A term,
    so V(h) reduces exactly to κ·Δ·m_i (test_self_excluded_from_spillover).
    """
    t = targets.sort_values(["household_row", "m_i"], ascending=[True, False])
    g = t.groupby("household_row", sort=False)["m_i"]
    m_answerer = g.transform("first")     # rows are sorted desc within group, so "first" is argmax
    m_total = g.transform("sum")
    m_others = m_total - m_answerer
    v = kappa * delta * (m_answerer + beta * m_others)
    per_person = pd.DataFrame({"household_row": t["household_row"].to_numpy(),
                               "v": v.to_numpy()}).drop_duplicates("household_row")
    per_person = per_person.rename(columns={"household_row": "hh_id", "v": "value"})
    out = households_with_targets.merge(per_person, on="hh_id", how="left")
    out["value"] = out["value"].fillna(0.0)
    return out


def value_turfs(households_valued: pd.DataFrame, *,
                doors_per_hour: float = C.CANVASS_DOORS_PER_HOUR) -> pd.DataFrame:
    """Roll household values up to turf level; add the compactness diagnostics
    and the hours-per-vote figure that makes the ranking legible (§3.2, §3.3).

    turf_id == -1 (ungeocoded households, from build_turfs) is dropped here,
    not just left NaN on diameter -- it is one giant aggregate of every
    scattered, un-walkable household in the county, so its summed value
    swamps every real turf's and would rank first despite being nothing a
    canvasser could ever walk. Reported as a separate figure by the caller
    (main() below), never mixed into the ranked table.

    Compactness here is an approximation deliberately kept dependency-free
    (no scipy.spatial): diameter is exact max pairwise distance (fine at
    <=TURF_MAX_DOORS households per turf); doors_per_km divides by that
    diameter rather than a true walking path length.
    """
    rows = []
    for tid, g in households_valued[households_valued["turf_id"] != -1].groupby("turf_id"):
        n_doors = len(g)
        n_targets = int(g["n_targets"].sum())
        value = float(g["value"].sum())
        if n_doors < 2 or g["lat"].isna().all():
            diameter_m = float("nan")
            doors_per_km = float("nan")
        else:
            x, y = planar_metres(g["lat"].to_numpy(), g["lon"].to_numpy())
            dx = x[:, None] - x[None, :]
            dy = y[:, None] - y[None, :]
            diameter_m = float(np.hypot(dx, dy).max())
            doors_per_km = n_doors / (diameter_m / 1000.0) if diameter_m > 0 else float("nan")
        canvasser_hours = n_doors / doors_per_hour
        rows.append({
            "turf_id": tid,
            "n_doors": n_doors,
            "n_targets": n_targets,
            "value_dem_ballots": value,
            "diameter_m": diameter_m,
            "doors_per_km": doors_per_km,
            "canvasser_hours": canvasser_hours,
            "hours_per_ballot": (canvasser_hours / value) if value > 0 else float("inf"),
            "county": g["county"].mode().iat[0] if not g["county"].mode().empty else None,
            "ed_keys_touched": sorted(g["ed_key"].dropna().unique().tolist()),
            "is_facility_share": float(g["is_facility"].mean()),
        })
    return pd.DataFrame(rows).sort_values("value_dem_ballots", ascending=False).reset_index(drop=True)


# -------------------------------------------------------------- randomization

def assign_arms(turfs: pd.DataFrame, *,
                control_fraction: float = C.CONTROL_FRACTION,
                seed: int = C.ARM_ASSIGNMENT_SEED,
                buffer: bool = C.BUFFER_RING,
                superturf_size: int = 4) -> pd.DataFrame:
    """Geographically blocked randomization at the SUPER-TURF level (§6.3).

    Individual-turf randomization lets household spillover cross the arm
    boundary at every edge, since contiguous chunking means every turf is
    surrounded by other turfs (Eckles et al. 2017: residual bias scales with
    inter-cluster edge count, and contiguous chunking maximises it). Grouping
    `superturf_size` Hilbert-adjacent turfs into one randomization unit pushes
    the treated/control boundary out to a coarser scale; the buffer ring then
    drops the turfs immediately adjacent to a control super-turf from both
    arms, so they are neither treated nor analysed.

    Stratifies on (county, value decile) so arms are balanced on the thing
    being measured. Frozen seed: same seed -> identical arms, always.
    """
    t = turfs[turfs["turf_id"] != -1].copy()
    if t.empty:
        turfs = turfs.copy()
        turfs["arm"] = "unassigned"
        return turfs

    # turf_id is assigned in Hilbert-sort order by build_turfs, so grouping
    # turf_id ranges into fixed-size blocks gives spatially adjacent super-turfs
    # without needing to re-carry raw household points into the turf table.
    t = t.sort_values("turf_id").reset_index(drop=True)
    t["superturf"] = t.index // superturf_size

    t["value_decile"] = pd.qcut(t["value_dem_ballots"].rank(method="first"),
                                q=min(10, t["turf_id"].nunique()), labels=False, duplicates="drop")
    super_strata = (t.groupby("superturf")
                    .agg(county=("county", "first"), value_decile=("value_decile", "median"))
                    .reset_index())
    super_strata["stratum"] = (super_strata["county"].astype(str) + "|"
                               + super_strata["value_decile"].round().astype(str))

    rng = np.random.default_rng(seed)
    control_supers = set()
    for _, grp in super_strata.groupby("stratum"):
        ids = grp["superturf"].to_numpy().copy()
        rng.shuffle(ids)  # deterministic given seed: same seed -> same shuffle -> same arms
        k = max(0, round(len(ids) * control_fraction)) if control_fraction > 0 else 0
        control_supers.update(ids[:k])

    t["arm"] = np.where(t["superturf"].isin(control_supers), "control", "treatment")

    if buffer:
        # Any treated super-turf that is Hilbert-adjacent (immediately before
        # or after) a control super-turf is neither treated nor analysed.
        adjacent = set()
        for s in control_supers:
            adjacent.add(s - 1)
            adjacent.add(s + 1)
        buf_mask = t["superturf"].isin(adjacent - control_supers) & (t["arm"] == "treatment")
        t.loc[buf_mask, "arm"] = "buffer"

    out = turfs.merge(t[["turf_id", "arm", "superturf"]], on="turf_id", how="left")
    out["arm"] = out["arm"].fillna("unassigned")
    return out


# --------------------------------------------------------------------- CLI

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", choices=("catboost", "gtn"), default="catboost")
    ap.add_argument("--persons", type=Path, default=C.PERSONS_PARQUET)
    ap.add_argument("--acs", type=Path, default=C.ACS_FEATURES_PARQUET)
    ap.add_argument("--serve-history", type=Path, default=C.HISTORY_SERVE_PARQUET)
    ap.add_argument("--train-history", type=Path, default=C.HISTORY_FEATURES_PARQUET)
    ap.add_argument("--support-side", choices=("dem", "rep"), default=C.SUPPORT_SIDE)
    ap.add_argument("--out-turfs", type=Path, default=C.TURFS_PARQUET)
    ap.add_argument("--out-assignment", type=Path, default=C.TURF_ASSIGNMENT_PARQUET)
    args = ap.parse_args()

    print("Loading targets...")
    targets = load_targets(model=args.model, persons_path=args.persons, acs_path=args.acs,
                           serve_history=args.serve_history, train_history=args.train_history,
                           support_side=args.support_side)
    print(f"  {len(targets):,} target voters, mean m_i={targets['m_i'].mean():.4f}")

    print("Building households...")
    hh = build_households(targets)
    print(f"  {len(hh):,} households ({int(hh['is_facility'].sum()):,} in facility-sized buildings)")

    print("Building turfs...")
    hh = build_turfs(hh)
    n_turfs = hh.loc[hh["turf_id"] != -1, "turf_id"].nunique()
    n_ungeo = int((hh["turf_id"] == -1).sum())
    print(f"  {n_turfs:,} turfs; {n_ungeo:,} ungeocoded households excluded")

    print("Valuing turfs...")
    hh = value_households(hh, targets)
    turf_table = value_turfs(hh)
    ungeo_value = float(hh.loc[hh["turf_id"] == -1, "value"].sum())
    print(f"  total value (walkable turfs only): "
         f"{turf_table['value_dem_ballots'].sum():.1f} expected additional ballots")
    print(f"  additional value stranded in ungeocoded households (not walkable, "
         f"not ranked, not written): {ungeo_value:.1f} -- fix geocoding to recover this")

    print("Assigning experimental arms...")
    turf_table = assign_arms(turf_table)
    print(turf_table["arm"].value_counts())

    fp = population_fingerprint(pd.read_parquet(args.persons, columns=["person_uuid"]))
    write_stamped(turf_table, args.out_turfs, fp, model=args.model)
    print(f"Wrote {args.out_turfs} ({len(turf_table):,} rows)")

    assignment = hh[hh["turf_id"] != -1].merge(
        targets[["person_row", "household_row", "m_i"]],
        left_on="hh_id", right_on="household_row", how="inner")
    assignment = assignment[["person_row", "hh_id", "addr_id", "turf_id", "m_i"]]
    write_stamped(assignment, args.out_assignment, fp, model=args.model)
    print(f"Wrote {args.out_assignment} ({len(assignment):,} rows)")


if __name__ == "__main__":
    main()
