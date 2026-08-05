#!/usr/bin/env python3
"""turfs.py — Phase 1 of objective #2: canvass turfs, ranked by expected
additional Democratic ballots. No retrain, no graph embeddings.

Consumes the served CatBoost/GTN scores the same way score_voters.py and
export_scores.py do; produces nothing that feeds back into either model. See
model/turfs/README.md for the household-vs-address distinction this module
depends on and why it is NOT the DGI/HDBSCAN clustering half of objective #2.

Three outputs, all fingerprint-stamped against the persons table they were
built from (persons_io.write_stamped), all under config.ARTIFACTS:

  turfs.parquet             one row per WALKABLE turf — non-PII, shareable
  facilities.parquet        one row per apartment building / facility — a
                             separate tactic track (lobby, phone, relational),
                             deliberately NOT in the walk list, non-PII
  turf_assignment.parquet   one row per target voter — joins back to persons_io,
                             not shareable

Turfs are ranked on value_net_margin (expected two-party margin), not on gross
supporting ballots: mobilisation turns out whoever answers the door, so a
0.55-lean voter is worth +0.10 net, not +0.55. Gross is kept alongside it.

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
    lat, lon, has_geo, ed_key, county, household_size, p_turnout, p_support,
    p_oppose, and two masses -- m_i (§2.4's movability-weighted mass) and
    m_net_i, its two-party-margin twin. Both are normalised to mean 1 over
    exactly this pool via a single shared movability_weight call.
    """
    if support_side not in ("dem", "rep"):
        raise ValueError(f"support_side must be 'dem' or 'rep', got {support_side!r}")
    if support_min <= 0.5:
        # m_net_i = w * (p_support - p_oppose) goes negative below a coin flip, and
        # value_turfs sums it -- a door that actively helps the other side would
        # cancel a good door invisibly instead of showing up as a bad target.
        raise ValueError(
            f"support_min must exceed 0.5, got {support_min}. Below it the net-margin "
            f"mass turns negative and nets out silently inside the turf sum.")

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
        oppose_raw = (s["rep_lean"] if support_side == "dem" else s["dem_lean"]).to_numpy()
    elif model == "gtn":
        gtn = load_gtn_scores(persons)
        turnout_raw = gtn["turnout_propensity"].to_numpy(np.float32)
        support_raw = (gtn["p_dem_lean"] if support_side == "dem"
                      else gtn["p_rep_lean"]).to_numpy(np.float32)
        oppose_raw = (gtn["p_rep_lean"] if support_side == "dem"
                     else gtn["p_dem_lean"]).to_numpy(np.float32)
    else:
        raise ValueError(f"model must be 'catboost' or 'gtn', got {model!r}")

    p_turnout = to_base_rate(turnout_raw, C.SERVE_BASE_RATE, "turnout")
    assert_serve_base_rate(p_turnout)

    # household_size (registered voters sharing a household_uuid, from
    # features_person.assemble) is what build_households flags facilities on. It is
    # required, not optional: the previous address-level rule could never fire, and
    # a quiet fallback to a derived count is exactly how that survived a full
    # production run with is_facility_share == 0.0 on all 1,652 turfs.
    if "household_size" not in persons.columns:
        raise ValueError(
            "persons frame has no `household_size` column -- build_households needs it "
            "to tell a 30-voter apartment building from a door. Rebuild persons.parquet "
            "(model/features_person.py) rather than deriving it here.")

    pool = pd.DataFrame({
        "person_row": np.arange(len(persons), dtype=np.int64),
        "household_row": persons["household_row"].to_numpy(),
        "household_size": persons["household_size"].to_numpy(),
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
        "p_oppose": oppose_raw,
    })

    mask = ((pool["p_turnout"] >= movable_lo) & (pool["p_turnout"] <= movable_hi)
           & (pool["p_support"] >= support_min))
    pool = pool.loc[mask].reset_index(drop=True)
    if pool.empty:
        raise ValueError("no voters survive the movable/support filter — check "
                         "movable_lo/hi and support_min against the served scores")

    # One weight vector, two masses. m_i counts gross supporting ballots (what the
    # ranking used to be built on, kept for comparison); m_net_i counts two-party
    # MARGIN, so a 0.55/0.35 voter is worth 0.20 rather than 0.55 -- mobilisation is
    # blunt and turns out whoever answers. p_support - p_oppose rather than 2p-1
    # because the party head is 3-class (dem_lean/rep_lean/other): 2p-1 would charge
    # the campaign for minor-party registrants as though they voted the other way.
    w = movability_weight(pool["p_turnout"].to_numpy())
    pool["m_i"] = w * pool["p_support"].to_numpy()
    pool["m_net_i"] = w * (pool["p_support"].to_numpy() - pool["p_oppose"].to_numpy())
    return pool


# --------------------------------------------------------- households/addr

def build_households(targets: pd.DataFrame, *,
                     facility_min_voters: int = C.TURF_FACILITY_MIN_VOTERS) -> pd.DataFrame:
    """One row per household THAT CONTAINS A TARGET VOTER.

    hh_id is persons.parquet's own `household_row` -- the real per-record
    household from the ETL, not something reconstructed. addr_id is a
    building-level grouping (county|city|street_name|address_number), the
    same string graph_build.py's same_address_edges groups on. The two are
    genuinely different relations: a six-unit building CAN be six households
    and one address. Unioning them (the tempting one-liner) would make every
    multi-unit building a single facility-sized "household" and silently zero
    spillover for the ordinary households inside it.
    test_apartment_building_is_not_one_household pins that.

    is_facility answers a different question: is this row a door a canvasser
    can knock, or a building they cannot get into? Two independent signals,
    OR'd, because either shape can occur depending on the source:

      household_size > cap   one ETL row IS the whole building. This is what
                             Nassau/Suffolk actually produce -- households are
                             keyed on household_uuid, so 10 WELWYN RD GREAT
                             NECK is ONE row with 30 registered voters.
      addr_size > cap        the source split units into separate households
                             that share a street address.

    Only the second test existed before, and against this ETL it can never
    fire (distinct households per address is always ~1), so is_facility was
    False for every household ever built and is_facility_share was exactly
    0.0 across all 1,652 turfs in production. Keep both: the addr_size branch
    is dead here but correct for any source that does split units.

    NOT n_targets -- targets are the post-filter subset, so a 30-voter
    building with 4 movable Dems would read as a 4-person house and slip
    through. household_size counts registered voters, filter or no filter.
    """
    if "household_size" not in targets.columns:
        raise ValueError(
            "build_households: targets frame has no `household_size` column -- without "
            "it facilities cannot be told from doors (see load_targets).")

    addr = (targets["county"].astype(str) + "|" + targets["city"].astype(str) + "|"
           + targets["street_name"].astype(str) + "|" + targets["address_number"].astype(str))
    t = targets.assign(addr=addr)

    hh = t.groupby("household_row", sort=False).agg(
        lat=("lat", "mean"), lon=("lon", "mean"), has_geo=("has_geo", "max"),
        ed_key=("ed_key", "first"), county=("county", "first"),
        addr=("addr", "first"), n_targets=("household_row", "size"),
        # constant within the group; max is defensive against a NaN
        household_size=("household_size", "max"),
    ).reset_index().rename(columns={"household_row": "hh_id"})

    addr_size = t.drop_duplicates("household_row").groupby("addr")["household_row"].transform("size")
    addr_lookup = (t.drop_duplicates("household_row")
                   .assign(addr_size=addr_size)[["household_row", "addr", "addr_size"]]
                   .rename(columns={"household_row": "hh_id"}))
    hh = hh.merge(addr_lookup[["hh_id", "addr_size"]], on="hh_id", how="left")
    hh["addr_id"] = pd.factorize(hh["addr"])[0]
    hh["is_facility"] = ((hh["household_size"] > facility_min_voters)
                         | (hh["addr_size"] > facility_min_voters))
    # keep household_size/addr_size on the frame: the flag they produce decides
    # whether a row is walked or phoned, and that has to stay auditable downstream.
    return hh.drop(columns=["addr"])


TURF_ID_UNGEOCODED = -1
TURF_ID_FACILITY = -2


def split_track(households: pd.DataFrame) -> pd.DataFrame:
    """Tag each household `walk`, `facility`, or `ungeocoded`.

    Three populations that need three different tactics, and mixing them is
    what made the top-ranked turf in the county unwalkable:

      walk        a door. Gets chunked into turfs and ranked on hours/ballot.
      facility    a building. A canvasser cannot knock a locked lobby, so
                  counting a 58-voter tower as one 3-minute door made
                  apartment-dense turfs look like the most efficient in the
                  file (turf 1443: 39 buildings holding 896 of 1,136 targets,
                  best hours_per_ballot in Nassau). Ranked separately, for
                  lobby access / phone / relational organising.
      ungeocoded  no lat/lon, cannot be walked from a Hilbert order at all.

    Ungeocoded wins over facility: a building nobody can find is not a
    building anybody can canvass either.
    """
    out = households.copy()
    ungeo = out["has_geo"] != 1
    out["track"] = np.where(ungeo, "ungeocoded",
                            np.where(out["is_facility"], "facility", "walk"))
    return out


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
                merge_max_metres: float = C.TURF_MERGE_MAX_METRES,
                order: int = 16) -> pd.DataFrame:
    """Hilbert-sort the WALK track, greedily chunk into turfs, then merge any
    turf left under min_doors into its nearest neighbour turf.

    Only `track == "walk"` is chunked. The other two keep sentinel ids and are
    handled elsewhere, rather than being silently dropped or piled onto a
    shared centroid:

      -1  ungeocoded households -- cannot be walked from a Hilbert order
      -2  facilities -- buildings, ranked by rank_facilities() instead

    Requires the `track` column from split_track().

    Returns `households` with a `turf_id` column added.
    """
    if households["hh_id"].duplicated().any():
        raise ValueError("build_turfs: households must be one row per hh_id")
    if "track" not in households.columns:
        raise ValueError("build_turfs: households needs a `track` column -- "
                         "call split_track() first.")

    geo = households[households["track"] == "walk"].copy()
    rest = households[households["track"] != "walk"].copy()
    rest["turf_id"] = np.where(rest["track"] == "facility",
                               TURF_ID_FACILITY, TURF_ID_UNGEOCODED)

    if geo.empty:
        return rest

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

    geo = _merge_undersized_turfs(geo, min_doors=min_doors, max_doors=max_doors,
                                  merge_max_metres=merge_max_metres)

    out = pd.concat([geo.drop(columns=["_x", "_y", "_hilbert"]), rest],
                    ignore_index=True, sort=False)
    return out


def _merge_undersized_turfs(geo: pd.DataFrame, *, min_doors: int, max_doors: int,
                            merge_max_metres: float = C.TURF_MERGE_MAX_METRES) -> pd.DataFrame:
    """Sweep every turf below min_doors (not just the trailing one) into its
    nearest-centroid neighbour with spare capacity, ascending by size so the
    smallest fragments merge first.

    A candidate further than merge_max_metres away is not a neighbour. Without
    that ceiling this stage silently undoes build_turfs' own work: the loop
    there force-splits on a break_metres gap (water crossing, highway,
    subdivision edge), and then nearest-centroid-at-any-distance merges the
    fragment straight back across it -- measured on real data, that produced
    turfs spanning the whole county.

    The ceiling is TURF_MERGE_MAX_METRES, NOT break_metres, because the two
    measure different things: break_metres is the gap between two
    Hilbert-consecutive HOUSEHOLDS (median 30m on this file), while this
    compares turf CENTROIDS, which for two adjacent full-size turfs are about
    a turf-diameter apart. Reusing 400m here rejected almost every legitimate
    merge and left 72% of turfs under min_doors.

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
        near = d <= merge_max_metres
        if not near.any():
            continue  # nothing within reach: stays undersized, as documented above
        cand_ids, d = cand_ids[near], d[near]
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

def _household_value(targets: pd.DataFrame, mass_col: str, *,
                     beta: float, delta: float, kappa: float,
                     max_ratio: float) -> pd.DataFrame:
    """(hh_id, v) for one mass column. See value_households for the formula."""
    t = targets.sort_values(["household_row", mass_col], ascending=[True, False])
    g = t.groupby("household_row", sort=False)[mass_col]
    m_answerer = g.transform("first")     # rows are sorted desc within group, so "first" is argmax
    m_total = g.transform("sum")
    m_others = m_total - m_answerer
    # Cap spillover mass relative to the answerer's own. A(h) is the argmax, so
    # every m_j <= m_A and a 1- or 2-person household can never reach the cap --
    # those are exactly the sizes Nickerson measured, and they stay bit-identical.
    spill = np.minimum(m_others.to_numpy(), max_ratio * m_answerer.to_numpy())
    v = kappa * delta * (m_answerer.to_numpy() + beta * spill)
    return (pd.DataFrame({"hh_id": t["household_row"].to_numpy(), "v": v})
            .drop_duplicates("hh_id"))


def value_households(households_with_targets: pd.DataFrame, targets: pd.DataFrame, *,
                     beta: float = C.SPILLOVER_BETA,
                     delta: float = C.CANVASS_DIRECT_EFFECT,
                     kappa: float = C.CANVASS_CONTACT_RATE,
                     max_ratio: float = C.SPILLOVER_MAX_RATIO) -> pd.DataFrame:
    """Per-household expected additional ballots, gross and net (§3.1).

        V(h) = κ · Δ · (m_A + β · min(Σ_{j≠A} m_j, max_ratio · m_A))

    A(h) = the household member with the largest mass -- the one worth asking
    for. Everyone else in the household gets the spillover discount β.
    Self-excluded by construction: a 1-person household has no j != A term,
    so V(h) reduces exactly to κ·Δ·m (test_self_excluded_from_spillover).

    Two columns out, from two independent passes:

      value      gross supporting ballots, from m_i. Unchanged from the
                 original formula apart from the cap, so it stays comparable
                 to what production ranked on before.
      value_net  two-party margin, from m_net_i.

    Each pass takes its own argmax. A 0.56-lean voter can top the gross
    ranking while contributing almost nothing net, and you do not want the
    canvasser sent to ask for them on a list ranked by margin.

    The β cap is a BACKSTOP, not the main defence against apartment towers --
    split_track pulls whole buildings out of the walk list before valuation
    matters. It is here for genuine multigenerational households, which are
    real and are still well past anything Nickerson's two-voter estimate
    covers. Do not remove either one on the grounds that the other exists.
    """
    gross = _household_value(targets, "m_i", beta=beta, delta=delta, kappa=kappa,
                             max_ratio=max_ratio).rename(columns={"v": "value"})
    net = _household_value(targets, "m_net_i", beta=beta, delta=delta, kappa=kappa,
                           max_ratio=max_ratio).rename(columns={"v": "value_net"})
    out = (households_with_targets
           .merge(gross, on="hh_id", how="left")
           .merge(net, on="hh_id", how="left"))
    out["value"] = out["value"].fillna(0.0)
    out["value_net"] = out["value_net"].fillna(0.0)
    return out


def value_turfs(households_valued: pd.DataFrame, *,
                doors_per_hour: float = C.CANVASS_DOORS_PER_HOUR) -> pd.DataFrame:
    """Roll household values up to turf level; add the compactness diagnostics
    and the hours-per-vote figure that makes the ranking legible (§3.2, §3.3).

    Only real turf ids (>= 0) are rolled up. Both sentinels are excluded, for
    the same reason but with different remedies:

      -1 ungeocoded  one giant aggregate of every scattered household in the
                     county, whose summed value would swamp every real turf
                     and rank first despite being nothing anyone can walk.
      -2 facility    apartment towers. Counting them here is what let a turf
                     be ranked #1 on doors that cannot be knocked; they are
                     ranked on their own by rank_facilities().

    Dropping facilities is the line that makes n_doors, canvasser_hours and
    hours_per_ballot honest -- they now describe only doors that exist.
    Both are reported separately by main(), never mixed into the ranked table.

    Compactness here is an approximation deliberately kept dependency-free
    (no scipy.spatial): diameter is exact max pairwise distance (fine at
    <=TURF_MAX_DOORS households per turf); doors_per_km divides by that
    diameter rather than a true walking path length.
    """
    rows = []
    for tid, g in households_valued[households_valued["turf_id"] >= 0].groupby("turf_id"):
        n_doors = len(g)
        n_targets = int(g["n_targets"].sum())
        value = float(g["value"].sum())
        value_net = float(g["value_net"].sum())
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
            # targets_per_door is the symptom that exposed the facility bug: the
            # top-ranked turf ran 7.5 against a file median of 1.59. Kept as a
            # permanent column so the next regression is visible in the output.
            "targets_per_door": n_targets / n_doors if n_doors else float("nan"),
            "value_dem_ballots": value,
            "value_net_margin": value_net,
            "diameter_m": diameter_m,
            "doors_per_km": doors_per_km,
            "canvasser_hours": canvasser_hours,
            # None, not inf: Postgres takes 'Infinity' but JSON.stringify in the map
            # API turns it into null anyway. Make the null deliberate and typed.
            "hours_per_ballot": (canvasser_hours / value) if value > 0 else None,
            "hours_per_net_margin": (canvasser_hours / value_net) if value_net > 0 else None,
            "county": g["county"].mode().iat[0] if not g["county"].mode().empty else None,
            "ed_keys_touched": sorted(g["ed_key"].dropna().unique().tolist()),
        })
    return pd.DataFrame(rows).sort_values("value_net_margin", ascending=False).reset_index(drop=True)


def rank_facilities(households_valued: pd.DataFrame) -> pd.DataFrame:
    """One row per apartment building / facility, ranked by net margin.

    These are not doors, so they carry no hours figure -- what it costs to
    reach a locked lobby is an organising question (building captain, phone,
    relational), not 3 minutes at 20 doors/hour. Ranking them separately is
    the whole point: the density that made them distort the walk list is real
    and worth pursuing, just not by knocking.

    nearest_turf_id ties each building to the walk turf whose canvassers are
    already closest, so a field organiser can hand both to the same team.
    """
    fac = households_valued[households_valued["turf_id"] == TURF_ID_FACILITY].copy()
    if fac.empty:
        return pd.DataFrame(columns=[
            "facility_id", "hh_id", "n_targets", "household_size", "value_dem_ballots",
            "value_net_margin", "lat", "lon", "county", "ed_key", "nearest_turf_id"])

    # Turf centroids from the households that actually built them, not from the
    # turf table (which carries no coordinates).
    walk = households_valued[households_valued["turf_id"] >= 0]
    if walk.empty:
        fac["nearest_turf_id"] = pd.NA
    else:
        cent = walk.groupby("turf_id")[["lat", "lon"]].mean()
        # Project both sets together so the shared lat0 makes the metres comparable.
        lat = np.concatenate([cent["lat"].to_numpy(), fac["lat"].to_numpy()])
        lon = np.concatenate([cent["lon"].to_numpy(), fac["lon"].to_numpy()])
        x, y = planar_metres(lat, lon)
        k = len(cent)
        cx, cy, fx, fy = x[:k], y[:k], x[k:], y[k:]
        d = np.hypot(fx[:, None] - cx[None, :], fy[:, None] - cy[None, :])
        fac["nearest_turf_id"] = cent.index.to_numpy()[np.argmin(d, axis=1)]

    fac = fac.sort_values("value_net", ascending=False).reset_index(drop=True)
    fac["facility_id"] = np.arange(len(fac), dtype=np.int64)
    out = fac.rename(columns={"value": "value_dem_ballots", "value_net": "value_net_margin"})
    return out[["facility_id", "hh_id", "n_targets", "household_size", "value_dem_ballots",
                "value_net_margin", "lat", "lon", "county", "ed_key", "nearest_turf_id"]]


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

    Raises rather than emitting "unassigned" for a turf it cannot place.
    017_turfs.sql constrains arm to (treatment, control, buffer), so that
    string was a Postgres CHECK violation thrown from inside write_supabase's
    15-minute transaction -- after the TRUNCATE had already dropped both
    tables. Unreachable today only because value_turfs pre-filters the
    sentinel ids; one refactor away from being reachable. Fail here, where the
    turf table is still in memory and nothing has been destroyed.
    """
    t = turfs[turfs["turf_id"] >= 0].copy()
    if t.empty:
        raise ValueError("assign_arms: no turfs with a real turf_id -- every row is a "
                         "sentinel (-1 ungeocoded / -2 facility) or the table is empty. "
                         "value_turfs should have filtered these out already.")

    # turf_id is assigned in Hilbert-sort order by build_turfs, so grouping
    # turf_id ranges into fixed-size blocks gives spatially adjacent super-turfs
    # without needing to re-carry raw household points into the turf table.
    t = t.sort_values("turf_id").reset_index(drop=True)
    t["superturf"] = t.index // superturf_size

    # Stratify on the column the ranking is actually built from. Switched from
    # value_dem_ballots to value_net_margin on 2026-08-03, which changes arm
    # assignment even under the frozen seed -- safe only because no arm had been
    # fielded (the turf tables were first written 2026-08-02). If canvassers have
    # since been sent out, DO NOT change this again: re-randomising mid-experiment
    # invalidates it just as surely as changing ARM_ASSIGNMENT_SEED.
    t["value_decile"] = pd.qcut(t["value_net_margin"].rank(method="first"),
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
    unplaced = out["arm"].isna()
    if unplaced.any():
        raise ValueError(
            f"assign_arms: {int(unplaced.sum())} turf(s) came back without an arm "
            f"(turf_id {out.loc[unplaced, 'turf_id'].head(5).tolist()}). Every row "
            f"reaching here must get treatment/control/buffer -- see the CHECK "
            f"constraint in supabase/migrations/017_turfs.sql.")
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
    ap.add_argument("--out-facilities", type=Path, default=C.FACILITIES_PARQUET)
    args = ap.parse_args()

    print("Loading targets...")
    targets = load_targets(model=args.model, persons_path=args.persons, acs_path=args.acs,
                           serve_history=args.serve_history, train_history=args.train_history,
                           support_side=args.support_side)
    print(f"  {len(targets):,} target voters, mean m_i={targets['m_i'].mean():.4f}, "
         f"mean m_net_i={targets['m_net_i'].mean():.4f}")

    print("Building households...")
    hh = build_households(targets)
    hh = split_track(hh)
    # Evidence for TURF_FACILITY_MIN_VOTERS, printed every run so the threshold can
    # be moved on the real distribution rather than on the round number it started at.
    q = hh["household_size"].quantile([0.90, 0.95, 0.99, 0.999])
    print(f"  {len(hh):,} households; household_size p90={q.iloc[0]:.0f} "
         f"p95={q.iloc[1]:.0f} p99={q.iloc[2]:.0f} p99.9={q.iloc[3]:.0f} "
         f"max={hh['household_size'].max():.0f}")
    tracks = hh["track"].value_counts()
    fac_targets = int(hh.loc[hh["track"] == "facility", "n_targets"].sum())
    print(f"  track: " + ", ".join(f"{k}={v:,}" for k, v in tracks.items())
         + f"  (facilities hold {fac_targets:,} targets, "
           f"{100 * fac_targets / max(len(targets), 1):.1f}% of the pool)")
    if not (hh["track"] == "facility").any():
        print("  !! no facilities detected -- verify against a known building before "
              "trusting this; is_facility was silently 0 for every turf until 2026-08-03")

    print("Building turfs...")
    hh = build_turfs(hh)
    n_turfs = hh.loc[hh["turf_id"] >= 0, "turf_id"].nunique()
    n_ungeo = int((hh["turf_id"] == TURF_ID_UNGEOCODED).sum())
    n_fac = int((hh["turf_id"] == TURF_ID_FACILITY).sum())
    print(f"  {n_turfs:,} turfs; {n_ungeo:,} ungeocoded and {n_fac:,} facility "
         f"households held out of the walk list")

    print("Valuing turfs...")
    hh = value_households(hh, targets)
    turf_table = value_turfs(hh)
    facilities = rank_facilities(hh)
    if not facilities.empty:
        near = facilities["nearest_turf_id"].value_counts()
        turf_table["n_facilities_nearby"] = (turf_table["turf_id"].map(near)
                                             .fillna(0).astype(int))
    else:
        turf_table["n_facilities_nearby"] = 0
    ungeo_value = float(hh.loc[hh["turf_id"] == TURF_ID_UNGEOCODED, "value_net"].sum())
    fac_value = float(hh.loc[hh["turf_id"] == TURF_ID_FACILITY, "value_net"].sum())
    print(f"  walk list: {turf_table['value_net_margin'].sum():.1f} net margin "
         f"({turf_table['value_dem_ballots'].sum():.1f} gross ballots) over "
         f"{int(turf_table['n_doors'].sum()):,} doors, "
         f"{turf_table['targets_per_door'].mean():.2f} targets/door")
    print(f"  facilities (ranked separately, not walkable): {fac_value:.1f} net margin "
         f"across {len(facilities):,} buildings")
    print(f"  stranded in ungeocoded households (not ranked, not written): "
         f"{ungeo_value:.1f} -- fix geocoding to recover this")

    print("Assigning experimental arms...")
    turf_table = assign_arms(turf_table)
    print(turf_table["arm"].value_counts())

    fp = population_fingerprint(pd.read_parquet(args.persons, columns=["person_uuid"]))
    write_stamped(turf_table, args.out_turfs, fp, model=args.model)
    print(f"Wrote {args.out_turfs} ({len(turf_table):,} rows)")

    write_stamped(facilities, args.out_facilities, fp, model=args.model)
    print(f"Wrote {args.out_facilities} ({len(facilities):,} rows)")

    # Facilities keep their assignment rows: the voters are still targets, just
    # reached by a different tactic. turf_id -2 is what marks them in the list.
    assignment = hh[hh["turf_id"] != TURF_ID_UNGEOCODED].merge(
        targets[["person_row", "household_row", "m_i", "m_net_i"]],
        left_on="hh_id", right_on="household_row", how="inner")
    assignment = assignment[["person_row", "hh_id", "turf_id", "m_i", "m_net_i"]]
    write_stamped(assignment, args.out_assignment, fp, model=args.model)
    print(f"Wrote {args.out_assignment} ({len(assignment):,} rows)")


if __name__ == "__main__":
    main()
