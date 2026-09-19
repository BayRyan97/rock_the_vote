#!/usr/bin/env python3
"""backtest_windows.py — how much donation history is worth keeping?

`WINDOW_START = 2017-01-01` is a coverage decision (it is where the FEC bulk
files on disk begin), not a measured one. This measures it: four history arms,
scored on cycle-separated folds, ranked by lift-at-k against the baselines
anyone would reach for without a model.

Short answer: keep four years, do not chase older data, train on the most
recent complete fold, and the model does beat the free sort. Details below.

THE BASELINE TO BEAT IS RECENCY, NOT LIFETIME TOTAL. Sorting donors by days
since their last gift is free, needs no model, and is what a competent organiser
already does. A model that cannot beat that sort has not earned its complexity,
and the honest outcome is to ship the sort. Lifetime dollars is carried as a
second baseline because it is the sort people reach for INSTEAD, and it is
usually worse.

--------------------------------------------------------------------- the folds

2026 is a MIDTERM, and the design assumed that mattered: donation volume is
cycle-dependent -- 330,581 confirmed gifts in 2020 and 304,394 in 2024 against
287,449 in 2022 and 108,962 in 2018 -- so a model trained on a presidential
surge ought to over-predict a midterm. Folds are therefore reported separately
by cycle type and never pooled.

  fold   features through   label window        cycle type    base rate
  2018   2018-06-30         2018-08-08..12-31   midterm         35.11%
  2020   2020-06-30         2020-08-08..12-31   presidential    31.79%
  2022   2022-06-30         2022-08-08..12-31   midterm         22.18%
  2024   2024-06-30         2024-08-08..12-31   presidential    22.44%
  2026   2026-06-30         2026-08-08..12-31   midterm      NOT EVALUABLE

THE ASSUMPTION DID NOT SURVIVE ITS OWN TEST, which is what transfer_matrix()
exists to find out. Cycle type does not govern transfer. On three of the four
targets the cycle-matched trainer is NOT the best one, and scoring the 2022
midterm the cycle-matched 2018 midterm is the worst trainer available (lift@10
3.50) while the 2024 presidential is the best (3.95). Stable across three donor
partitions, so it is not one split's luck.

The base rates do not split by cycle either -- 22.18% (midterm) against 22.44%
(presidential). They decline as the donor file accumulates one-time givers, from
26,370 donors in 2018 to 118,484 in 2026.

WHAT DOES ORDER THE TRAINERS IS HOW MANY DONORS THEY SAW. Rank the three
off-diagonal trainers for each target by training-half size and by lift@10 and
the two orderings agree exactly on 2020, 2022 and 2024 -- 13,135 / 24,200 /
38,474 / 47,448 donors respectively. Only the 2018 target disagrees, and it is
the smallest and strangest fold (a 35% base rate against 22% later). Note that
2024 is the best trainer even for the 2018 target, six years EARLIER, which a
pure recency story cannot explain but a sample-size one can.

The practical rule is the same either way, because the most recent complete fold
is also the largest: TRAIN ON 2024. The distinction matters for what to expect
next -- if it is sample size, the model improves as the file grows, and
re-running this in 2028 should show 2026 overtaking 2024 as a trainer.

The 2026 model will therefore be trained on a presidential fold and used on a
midterm. Cycle type does not appear to affect RANKING, which is what targeting
uses. It may still affect the LEVEL of the predicted probability, and nothing
here tests that -- so use the score to sort and do not read it as a calibrated
chance of giving without recalibrating against 2026 outcomes as they arrive.

THE SEASONAL LABEL is what makes 2026 comparable at all: "gave between Aug 8 and
Dec 31 of year Y" is the same question in every fold, so 2026 being an unfinished
year stops being a censoring problem. It does not make 2026 evaluable -- see
below -- but it means the 2022 answer transfers to it directly.

WHY THE CUTOFF IS JUNE 30 AND NOT DECEMBER 31 OF THE PRIOR YEAR. The plan said
December. June is the operational match: this model gets used in August 2026, and
in August 2026 the newest data actually in hand runs to about 2026-06-30. FEC
quarterly filings for Apr-Jun are due 15 July and Jul-Sep are not due until 15
October, so an August run genuinely cannot see July. Measured on the current
cache: FEC's last confirmed gift is 2026-06-30, NY BOE reaches 2026-07-11 (its
July periodic, partially in), and July 2026 holds 3,095 gifts against a ~12,500
monthly baseline -- about 75% unreported. Cutting the historical folds at
December would hand them eight months of history the live run will not have, and
the backtest would flatter itself. --cutoff december reproduces the plan's
convention for comparison.

THE 2026 FOLD CANNOT BE SCORED, and this is not a limitation to work around. Its
label window is 2026-08-08 onward; today is 2026-08-12; the cache contains ONE
confirmed gift in that window and it is a forward-dated artifact. Scoring it
would produce a base rate near zero and a meaningless lift. This is exactly
safeguard 4 from the design -- "did not donate in 2026" is not yet a complete
negative outcome -- and the script refuses rather than reporting a number.

--------------------------------------------------------------- what it found

WINDOW LENGTH BARELY MATTERS FOR RANKING. On the 2022 fold, lift@10 is 4.07 for
2017+, 4.07 for 4y, 4.06 for 2y and 4.07 recency-weighted. AUC does separate --
0.888 / 0.887 / 0.875 / 0.889 -- and on the 2024 fold, where more history exists
to throw away, the 2y arm falls to 0.845 against 0.884. So the fourth year earns
its place in a calibrated probability and the fifth through seventh do not, and
NO arm changes who lands in the top decile.

  Recommendation: keep 4 years. 2017+ costs nothing and is what the fetchers
  already produce, so there is no reason to shorten it, but do not go looking
  for older data on the theory that it will help. It will not.

  Recency-weighting ties the hard cutoffs on every fold. It is a free parameter
  with no measured benefit; do not adopt it.

THE MODEL IS EARNED, WHICH WAS NOT A FOREGONE CONCLUSION. The plan's own test
was "if nothing beats days-since-last-gift, ship the sort." Something does: on
the 2022 fold the model reaches lift@10 4.07 against the recency sort's 2.86 --
42% more repeat donors in the top decile -- and AUC 0.888 against 0.805.

The third baseline is the interesting one. "How many past years did this donor
give in Aug-Dec" scores lift@10 3.41 on the 2022 fold, well ahead of recency's
2.86 despite being a cruder statistic. Seasonality is doing real work here, and
any feature set that omits it is leaving the single best free signal on the
table. Lifetime dollars -- the sort people actually reach for -- is the worst of
the three at 2.23.

------------------------------------------------------------------ the one limit

A true "all history" arm is NOT TESTABLE TODAY. The FEC bulk files on disk start
at 2017, so an arm reaching further back would be NY-BOE-only in its early years
and would measure SOURCE, not TIME. `effective_years` is printed per arm per fold
so a window clipped by the 2017 floor is visible as clipped rather than reading
as independent evidence -- on the 2018 fold every arm collapses to the same 1.5
years, which is why that fold cannot rank arms at all.

Usage:
    python model/donations/backtest_windows.py --donations <parquet>
    python model/donations/backtest_windows.py --folds 2022 --quick
    python model/donations/backtest_windows.py --cutoff december
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config as C  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from donor_value import (attach_sides, drop_cross_source_duplicates,  # noqa: E402
                         eligible_mask)

SIDE_DEM, SIDE_REP = "DEM", "REP"

# Aug 8 rather than Aug 1: it is where a campaign scoring its file in the second
# week of August actually stands, and it keeps the label window identical across
# folds so 2022 and 2024 answer the same question.
LABEL_START_MD = (8, 8)
LABEL_END_MD = (12, 31)

# The half-life for the recency-weighted arm. One year, because the measured
# repeat rate roughly halves over that span: 35.4% at two years out against
# 12.6% at four.
RECENCY_HALFLIFE_DAYS = 365.0


@dataclass(frozen=True)
class Fold:
    year: int
    cycle: str          # "midterm" | "presidential"
    governs: bool = False


FOLDS = [
    Fold(2018, "midterm"),
    Fold(2020, "presidential"),
    Fold(2022, "midterm"),
    # The most recent COMPLETE fold, and therefore -- per the transfer matrix,
    # not per its cycle type -- the one to train the 2026 model on.
    Fold(2024, "presidential", governs=True),
    Fold(2026, "midterm"),          # refused: label window has not happened
]

# arm name -> lookback in years (None = everything back to the data floor)
ARMS = {
    "all (2017+)": None,
    "last 4y": 4,
    "last 2y": 2,
    "recency-weighted": None,       # same rows as all, exponentially discounted
}

K_VALUES = (0.05, 0.10, 0.20)


# --------------------------------------------------------------------- loading

def load_gifts(donations_path: Path, dim_path: Path) -> pd.DataFrame:
    """Confirmed gifts only, with side/labor attached, as compact columns.

    `confirmed` is non-negotiable here for the same reason it is in
    donor_value.eligible_mask: a "possible" match agrees on name but has no
    address agreement, and POSSIBLE_CAP replicates one real gift across every
    same-surname voter on the block. Training a propensity model on those would
    teach it that common surnames give more.
    """
    print(f"Loading {donations_path}")
    d = pd.read_parquet(donations_path,
                        columns=["donor_key", "source", "donation_date",
                                 "amount", "committee", "confirmed"])
    dim = pd.read_parquet(dim_path)
    d = drop_cross_source_duplicates(d)
    d = attach_sides(d, dim)
    d = d[d["confirmed"].astype(bool)].copy()

    d["date"] = pd.to_datetime(d["donation_date"], errors="coerce")
    d["amount"] = pd.to_numeric(d["amount"], errors="coerce").fillna(0.0)
    bad = d["date"].isna().sum()
    if bad:
        print(f"  {bad:,} confirmed gifts have an unparseable date; dropped")
        d = d[d["date"].notna()]

    # Partisan dollars use the same quarantine the lean does -- labor payroll
    # deduction and corporate/trade access money are not preference. They still
    # count as GIVING for the propensity features; they just do not count as
    # partisan giving.
    d["partisan"] = eligible_mask(d)
    d["is_dem"] = d["partisan"] & (d["side"] == SIDE_DEM)
    d["is_rep"] = d["partisan"] & (d["side"] == SIDE_REP)

    keep = ["donor_key", "source", "date", "amount", "labor", "is_dem", "is_rep"]
    d = d[keep].reset_index(drop=True)
    print(f"  {len(d):,} confirmed gifts, {d['donor_key'].nunique():,} donors, "
          f"{d['date'].min().date()}..{d['date'].max().date()}")
    return d


def data_floor(gifts: pd.DataFrame) -> pd.Timestamp:
    return gifts["date"].min()


# -------------------------------------------------------------------- features

def _days(a: pd.Series, ref: pd.Timestamp) -> pd.Series:
    return (ref - a).dt.days.astype("float32")


def build_features(gifts: pd.DataFrame, cutoff: pd.Timestamp,
                   lookback_years: int | None, *, weighted: bool,
                   floor: pd.Timestamp) -> pd.DataFrame:
    """One row per donor in the arm's window, indexed by donor_key.

    Donors with NO gift inside a narrow arm are kept, not dropped, with censored
    features (`n_gifts` 0, `days_since_last` pinned to the window length). That
    is what a narrow window actually does to them operationally, and dropping
    them would hide the arm's cost instead of measuring it.
    """
    start = floor if lookback_years is None else max(
        floor, cutoff - pd.DateOffset(years=lookback_years))
    g = gifts[(gifts["date"] <= cutoff) & (gifts["date"] >= start)].copy()
    window_days = float((cutoff - start).days) or 1.0

    g["age"] = _days(g["date"], cutoff)
    if weighted:
        # 0.5 ** (age / halflife): a gift one half-life old counts half.
        w = np.power(0.5, g["age"] / RECENCY_HALFLIFE_DAYS).astype("float32")
    else:
        w = pd.Series(np.ones(len(g), dtype="float32"), index=g.index)
    g["w"] = w
    g["wamt"] = w * g["amount"].astype("float32")

    grp = g.groupby("donor_key", sort=False)
    f = pd.DataFrame(index=grp.size().index)
    f["n_gifts"] = grp["w"].sum().astype("float32")
    f["total_dollars"] = grp["wamt"].sum().astype("float32")
    f["max_gift"] = grp["amount"].max().astype("float32")
    f["mean_gift"] = (f["total_dollars"] / f["n_gifts"].clip(lower=1e-6)).astype("float32")
    f["days_since_last"] = grp["age"].min().astype("float32")
    f["days_since_first"] = grp["age"].max().astype("float32")

    for d in (90, 365, 730):
        recent = g[g["age"] <= d]
        rg = recent.groupby("donor_key", sort=False)
        f[f"n_gifts_{d}d"] = rg.size().reindex(f.index).fillna(0).astype("float32")
        f[f"dollars_{d}d"] = rg["amount"].sum().reindex(f.index).fillna(0).astype("float32")

    # Breadth over time. n_years is the plain version; n_cycles buckets into
    # two-year federal cycles, which is the unit giving actually clusters in.
    yr = g["date"].dt.year
    f["n_years_active"] = g.assign(y=yr).groupby("donor_key")["y"].nunique() \
                           .reindex(f.index).fillna(0).astype("float32")
    f["n_cycles_active"] = g.assign(c=(yr + (yr % 2)) // 2).groupby("donor_key")["c"] \
                            .nunique().reindex(f.index).fillna(0).astype("float32")

    # THE SEASONAL FEATURE. The label is "gave in Aug-Dec"; whether this donor
    # has ever given in an Aug-Dec before is the most direct prior available,
    # and it is the one a lifetime-total sort throws away.
    md = list(zip(g["date"].dt.month, g["date"].dt.day))
    in_season = np.array([(m, dd) >= LABEL_START_MD for m, dd in md])
    seas = g[in_season]
    sg = seas.groupby("donor_key", sort=False)
    f["n_seasonal_gifts"] = sg.size().reindex(f.index).fillna(0).astype("float32")
    f["n_seasonal_years"] = seas.assign(y=seas["date"].dt.year).groupby("donor_key")["y"] \
                                .nunique().reindex(f.index).fillna(0).astype("float32")

    # Partisan behaviour. Party-specific recency is carried separately because
    # "last gave to Democrats 90 days ago" and "last gave to Republicans 90 days
    # ago" are different facts about the same donor, and a donor who has both is
    # a third thing again.
    for side, col in (("is_dem", "dem"), ("is_rep", "rep")):
        s = g[g[side]]
        sgp = s.groupby("donor_key", sort=False)
        f[f"{col}_dollars"] = sgp["wamt"].sum().reindex(f.index).fillna(0).astype("float32")
        f[f"days_since_last_{col}"] = sgp["age"].min().reindex(f.index) \
                                         .fillna(window_days).astype("float32")
    denom = f["dem_dollars"] + f["rep_dollars"]
    prior = float(C.REVEALED_LEAN_PRIOR_DOLLARS)
    f["revealed_lean"] = np.where(denom > 0,
                                  (f["dem_dollars"] + prior / 2) / (denom + prior),
                                  np.nan).astype("float32")
    f["gave_both_sides"] = ((f["dem_dollars"] > 0) & (f["rep_dollars"] > 0)).astype("float32")
    f["labor_dollars"] = g[g["labor"]].groupby("donor_key")["wamt"].sum() \
                          .reindex(f.index).fillna(0).astype("float32")

    # Source breadth. Also the only place nyccfb's staleness shows up: its Data
    # Library ZIP stops at 2025, so a CFB-only donor is stale by construction.
    f["n_sources"] = grp["source"].nunique().astype("float32")
    for src in ("fec", "nyboe", "nyccfb"):
        f[f"src_{src}"] = g[g["source"] == src].groupby("donor_key").size() \
                           .reindex(f.index).fillna(0).clip(upper=1).astype("float32")

    f["window_days"] = np.float32(window_days)
    return f


def censored_frame(index: pd.Index, template: pd.DataFrame,
                   window_days: float) -> pd.DataFrame:
    """Rows for donors with no gift inside the arm's window."""
    z = pd.DataFrame(0.0, index=index, columns=template.columns, dtype="float32")
    for c in template.columns:
        if c.startswith("days_since"):
            z[c] = np.float32(window_days)
    z["revealed_lean"] = np.float32(np.nan)
    z["window_days"] = np.float32(window_days)
    return z


# ----------------------------------------------------------------- the outcome

def label_for(gifts: pd.DataFrame, year: int) -> set:
    """Donors who GAVE in the window. A refund is not a gift.

    19,838 confirmed rows carry a negative amount and 34,796 carry zero --
    refunds, redesignations and reattributions, which FEC files as Schedule A
    entries with the sign flipped. Counting a refund as "gave" labelled 139
    donors positive in 2022 whose only activity in the window was money going
    back OUT. Small, and exactly backwards.
    """
    lo = pd.Timestamp(year=year, month=LABEL_START_MD[0], day=LABEL_START_MD[1])
    hi = pd.Timestamp(year=year, month=LABEL_END_MD[0], day=LABEL_END_MD[1])
    m = (gifts["date"] >= lo) & (gifts["date"] <= hi) & (gifts["amount"] > 0)
    return set(gifts.loc[m, "donor_key"])


def cutoff_for(year: int, convention: str) -> pd.Timestamp:
    if convention == "june":
        return pd.Timestamp(year=year, month=6, day=30)
    return pd.Timestamp(year=year - 1, month=12, day=31)


# ------------------------------------------------------------------- scoring

def lift_at_k(y: np.ndarray, score: np.ndarray, k: float) -> float:
    """Positive rate in the top k fraction, divided by the overall rate."""
    n = max(1, int(round(len(score) * k)))
    top = np.argsort(-score, kind="stable")[:n]
    base = y.mean()
    return float(y[top].mean() / base) if base > 0 else float("nan")


def auc(y: np.ndarray, score: np.ndarray) -> float:
    from sklearn.metrics import roc_auc_score
    if len(np.unique(y)) < 2:
        return float("nan")
    return float(roc_auc_score(y, score))


def split_mask(keys: pd.Index, frac: float = 0.5, seed: int = 0) -> np.ndarray:
    """Deterministic donor-level split.

    Hashed on donor_key, not random, so the same donor lands in the same half in
    every arm and every fold -- otherwise arms would be compared on different
    people and the differences would be partly noise. `seed` perturbs the hash
    so the whole comparison can be repeated on a different partition, which is
    how you tell a real gap from one partition's luck.
    """
    h = pd.util.hash_pandas_object(pd.Series(list(keys)), index=False,
                                   hash_key=f"{seed:016d}").to_numpy()
    return (h % 1000) < int(frac * 1000)


def fit_score(Xtr, ytr, Xte, *, quick: bool, seed: int = 17) -> np.ndarray:
    from catboost import CatBoostClassifier
    m = CatBoostClassifier(
        iterations=150 if quick else 400,
        depth=6,
        learning_rate=0.08,
        loss_function="Logloss",
        random_seed=seed,
        verbose=0,
        allow_writing_files=False,
    )
    m.fit(Xtr, ytr)
    return m.predict_proba(Xte)[:, 1]


# ---------------------------------------------------------------------- report

def run_fold(gifts: pd.DataFrame, fold: Fold, floor: pd.Timestamp, *,
             convention: str, quick: bool) -> pd.DataFrame:
    cutoff = cutoff_for(fold.year, convention)
    pos = label_for(gifts, fold.year)

    # Population: every donor with a confirmed gift on or before the cutoff.
    # Defined on the WIDEST window so all arms are scored on the same people --
    # an arm that sees fewer donors must be penalised for it, not excused.
    universe = pd.Index(sorted(set(gifts.loc[gifts["date"] <= cutoff, "donor_key"])))
    y = np.fromiter((k in pos for k in universe), dtype=np.int8, count=len(universe))
    tr = split_mask(universe)

    hdr = (f"fold {fold.year}  ({fold.cycle}"
           f"{', TRAIN THE 2026 MODEL HERE' if fold.governs else ''})")
    print(f"\n{'=' * 78}\n{hdr}\n{'-' * 78}")
    print(f"  features through {cutoff.date()}   label "
          f"{fold.year}-08-08..{fold.year}-12-31")
    print(f"  {len(universe):,} donors   {int(y.sum()):,} gave in the window "
          f"({100 * y.mean():.2f}%)   train/eval {int(tr.sum()):,}/{int((~tr).sum()):,}")
    if y.sum() < 200:
        print("  REFUSED: fewer than 200 positives. The label window has not "
              "happened yet, or has not been reported yet.")
        return pd.DataFrame()

    rows = []
    for arm, lookback in ARMS.items():
        weighted = arm.startswith("recency")
        F = build_features(gifts, cutoff, lookback, weighted=weighted, floor=floor)
        missing = universe.difference(F.index)
        if len(missing):
            F = pd.concat([F, censored_frame(missing, F, float(F["window_days"].iloc[0]))])
        F = F.reindex(universe)

        start = floor if lookback is None else max(floor, cutoff - pd.DateOffset(years=lookback))
        eff_years = (cutoff - start).days / 365.25

        X = F.drop(columns=["window_days"])
        p = fit_score(X[tr], y[tr], X[~tr], quick=quick)
        ye = y[~tr]
        row = {"arm": arm, "eff_years": eff_years,
               "donors_with_history": int((F["n_gifts"] > 0).sum()),
               "auc": auc(ye, p)}
        for k in K_VALUES:
            row[f"lift@{int(k * 100)}"] = lift_at_k(ye, p, k)
        rows.append(row)

        # The two model-free sorts, scored on the same eval half, from the same
        # arm's features -- so a baseline is never advantaged by seeing history
        # the arm did not.
        if arm == "all (2017+)":
            for name, s in (("BASELINE recency", -F["days_since_last"].to_numpy()[~tr]),
                            ("BASELINE lifetime $", F["total_dollars"].to_numpy()[~tr]),
                            ("BASELINE seasonal yrs", F["n_seasonal_years"].to_numpy()[~tr])):
                b = {"arm": name, "eff_years": eff_years, "donors_with_history": np.nan,
                     "auc": auc(ye, s)}
                for k in K_VALUES:
                    b[f"lift@{int(k * 100)}"] = lift_at_k(ye, s, k)
                rows.append(b)

    out = pd.DataFrame(rows)
    out.insert(0, "fold", fold.year)
    out.insert(1, "cycle", fold.cycle)
    _print_table(out)
    return out


def _print_table(df: pd.DataFrame) -> None:
    print(f"\n  {'arm':<22} {'yrs':>5} {'w/hist':>9} {'AUC':>6} "
          + " ".join(f"{f'lift@{int(k*100)}':>8}" for k in K_VALUES))
    baselines = df[df["arm"].str.startswith("BASELINE")]
    models = df[~df["arm"].str.startswith("BASELINE")]
    kcols = [f"lift@{int(k * 100)}" for k in K_VALUES]
    for block in (models, baselines):
        if block.empty:
            continue
        for _, r in block.iterrows():
            wh = "" if pd.isna(r["donors_with_history"]) else f"{int(r['donors_with_history']):,}"
            print(f"  {r['arm']:<22} {r['eff_years']:>5.1f} {wh:>9} {r['auc']:>6.3f} "
                  + " ".join(f"{r[c]:>8.2f}" for c in kcols))
        print()


def transfer_matrix(gifts: pd.DataFrame, folds: list[Fold], floor: pd.Timestamp,
                    arm: str, *, convention: str, quick: bool,
                    seeds: tuple[int, ...] = (0, 101, 202)) -> None:
    """Train on one fold, score another. Does cycle type actually transfer?

    This is the claim the fold ladder rests on, and MEASURED, IT DOES NOT HOLD.
    On three of four targets the cycle-matched trainer is not the winner. What
    the ordering does track is training-fold SIZE -- see the module docstring.

    Run over several seeds because a single partition would have made a
    0.4-lift gap look like evidence when it could have been noise. The min-max
    range printed per cell is what makes that judgeable rather than asserted.

    The diagonal is trained and scored on disjoint halves of the same fold, so
    it is out-of-sample too and belongs on the same scale as the rest. It is the
    ceiling, not a comparison: 2026 has no diagonal available.
    """
    print(f"\n{'=' * 78}\ntransfer: train on one fold, score another   (arm: {arm})")
    print("-" * 78)
    lookback, weighted = ARMS[arm], arm.startswith("recency")

    base = {}
    for f in folds:
        cutoff = cutoff_for(f.year, convention)
        pos = label_for(gifts, f.year)
        universe = pd.Index(sorted(set(gifts.loc[gifts["date"] <= cutoff, "donor_key"])))
        y = np.fromiter((k in pos for k in universe), dtype=np.int8, count=len(universe))
        if y.sum() < 200:
            continue
        F = build_features(gifts, cutoff, lookback, weighted=weighted, floor=floor)
        missing = universe.difference(F.index)
        if len(missing):
            F = pd.concat([F, censored_frame(missing, F, float(F["window_days"].iloc[0]))])
        base[f.year] = (F.reindex(universe).drop(columns=["window_days"]), y,
                        universe, f.cycle)

    years = sorted(base)
    cells: dict[tuple[int, int], list[float]] = {(a, b): [] for a in years for b in years}
    for seed in seeds:
        split = {y: split_mask(base[y][2], seed=seed) for y in years}
        for a in years:
            Xa, ya, _, _ = base[a]
            for b in years:
                Xb, yb, _, _ = base[b]
                # Always score the EVAL half of the target fold, even when
                # a == b, so the diagonal is out-of-sample too and comparable.
                p = fit_score(Xa[split[a]], ya[split[a]], Xb[~split[b]],
                              quick=quick, seed=seed + 17)
                cells[(a, b)].append(lift_at_k(yb[~split[b]], p, 0.10))

    print(f"\n  lift@10 on the target fold's held-out half, "
          f"mean of {len(seeds)} donor partitions (min-max)")
    print(f"\n  {'train \\ score':<17}" + "".join(f"{y:>20}" for y in years))
    print(f"  {'':<17}" + "".join(f"{base[y][3][:4]:>20}" for y in years))
    for a in years:
        line = ""
        for b in years:
            v = cells[(a, b)]
            line += f"{f'{np.mean(v):.2f} ({min(v):.2f}-{max(v):.2f})':>20}"
        print(f"  {a} {base[a][3][:4]:<12}" + line)

    print("\n  The diagonal is the ceiling: a fold predicts itself best. 2026 has")
    print("  no diagonal, so what matters is the best OFF-diagonal trainer.")
    for b in years:
        off = [(a, np.mean(cells[(a, b)])) for a in years if a != b]
        best = max(off, key=lambda t: t[1])
        matched = [(a, m) for a, m in off if base[a][3] == base[b][3]]
        note = ""
        if matched:
            bm = max(matched, key=lambda t: t[1])
            note = (f"   cycle-matched best: {bm[0]} ({bm[1]:.2f})"
                    + ("  <- SAME" if bm[0] == best[0] else "  <- NOT the winner"))
        print(f"    score {b} ({base[b][3]}): best trainer {best[0]} "
              f"({base[best[0]][3]}, {best[1]:.2f}){note}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--donations", type=Path, default=C.CACHE / "donations.parquet")
    ap.add_argument("--dim", type=Path, default=C.COMMITTEE_DIM_PARQUET)
    ap.add_argument("--folds", type=int, nargs="+", default=[f.year for f in FOLDS])
    ap.add_argument("--cutoff", choices=["june", "december"], default="june",
                    help="june = operational match (default); december = the "
                         "prior-year convention from the plan")
    ap.add_argument("--quick", action="store_true", help="150 trees instead of 400")
    ap.add_argument("--no-transfer", action="store_true")
    ap.add_argument("--seeds", type=int, nargs="+", default=[0, 101, 202],
                    help="donor partitions for the transfer matrix; more than "
                         "one is what separates a real gap from split luck")
    ap.add_argument("--out", type=Path, default=None, help="write results CSV")
    args = ap.parse_args()

    if not args.donations.exists():
        raise SystemExit(f"{args.donations} not found")
    if not args.dim.exists():
        raise SystemExit(f"{args.dim} not found — run model/donations/committees.py")

    gifts = load_gifts(args.donations, args.dim)
    floor = data_floor(gifts)
    print(f"  data floor {floor.date()} — no arm can reach behind it, so "
          f"'all history' means 'all we have'")

    folds = [f for f in FOLDS if f.year in set(args.folds)]
    results = [r for r in (run_fold(gifts, f, floor, convention=args.cutoff,
                                    quick=args.quick) for f in folds) if not r.empty]
    if not results:
        raise SystemExit("no evaluable folds")
    allr = pd.concat(results, ignore_index=True)

    if not args.no_transfer:
        transfer_matrix(gifts, folds, floor, "all (2017+)",
                        convention=args.cutoff, quick=args.quick,
                        seeds=tuple(args.seeds))

    # Averaged over ALL folds, not midterms only. The transfer matrix showed
    # cycle type does not govern how a model transfers, and the base rates do
    # not split by cycle either, so filtering to midterms here would throw away
    # half the evidence to honour a distinction this file does not support.
    print(f"\n{'=' * 78}\nsummary — mean across evaluable folds\n{'-' * 78}")
    models = allr[~allr["arm"].str.startswith("BASELINE")]
    print(models.groupby("arm")[["auc"] + [f"lift@{int(k * 100)}" for k in K_VALUES]]
          .mean().round(3).to_string())
    print("\n  baselines")
    print(allr[allr["arm"].str.startswith("BASELINE")]
          .groupby("arm")[["auc", "lift@10"]].mean().round(3).to_string())

    if args.out:
        allr.to_csv(args.out, index=False)
        print(f"\n  wrote {args.out}")


if __name__ == "__main__":
    main()
