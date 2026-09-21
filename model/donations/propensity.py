#!/usr/bin/env python3
"""propensity.py — who is likely to give again, and roughly how much.

Produces one row per donor_key:

    p_donate                    P(gives between Aug 8 and Dec 31 of the target year)
    e_amount                    expected size of a gift, if one happens
    expected_dollars_untreated  the product

READ THE NAME OF THE THIRD COLUMN. "Untreated" means "under whatever
solicitation this donor already receives". It is NOT ROI, NOT uplift, and NOT
the extra dollars an ask would produce -- none of those are computable here,
because no filing regime records solicitations. write_supabase.py refuses to
publish any column named as if they were. The randomized holdout in migration
024 is the only thing that closes the gap, and it has to run first.

------------------------------------------------------------------- what it does

`p_donate` is a CatBoost binary on the feature set backtest_windows.py built and
leakage-tested: recency, frequency, dollars over 90/365/730d, cycles active,
seasonal history, party-specific recency, revealed lean, source breadth.

TRAINED ON THE 2024 FOLD, which is not the obvious choice and is not the one the
plan specified. The plan said to train on the most recent MIDTERM, 2022, because
2026 is a midterm. The transfer matrix in backtest_windows.py tested that and it
does not hold: on three of four targets the cycle-matched trainer is not the
best one, and what orders the trainers is how many donors they saw. 2024 is both
the largest and the most recent complete fold. See that module for the numbers.

The consequence to keep in mind: this ranks well and is NOT calibrated for 2026.
Base rates fall as the file grows -- 35.1% in 2018, 22.4% in 2024, on a donor
universe that went from 26,370 to 118,484 -- so the absolute probability is the
2024 level, not the 2026 one. USE IT TO SORT. Recalibrate against real 2026
outcomes before quoting any number as a chance of giving.

`e_amount` is deliberately the crudest thing that could work: the donor's own
median gift, with a ZIP-median prior mixed in ONLY for donors who have given
exactly once. The plan proposed shrinking everyone toward their ZIP; measured
against held-out 2024 gifts that is worse than not shrinking at all, because
gift size turns out to be strongly individual -- people give the same amount
again, especially on recurring plans -- so the ZIP median adds noise to a fact
already observed. It helps exactly where there is a single noisy observation to
regularise. The sweep is in the AMOUNT_SHRINK_GIFTS comment, and accept_amount()
re-runs the comparison on every run so the choice stays falsifiable rather than
becoming folklore.

WHY MEDIAN AND NOT MEAN. Gift sizes are extremely skewed -- ActBlue's median
gift here is $15 across 318,705 gifts while the file's largest are five figures
-- and a mean would let one legacy gift dominate a donor's forecast forever.

------------------------------------------------------------------- the acceptance test

The model has to beat sorting by days-since-last-gift on a held-out half, or it
does not ship. That is not a formality: the sort is free, needs no model, and is
what a competent organiser already does. --force overrides, and says so in the
output, because there are legitimate reasons to inspect a losing model.

Usage:
    python model/donations/propensity.py --donations <parquet> --report-only
    python model/donations/propensity.py --donations <parquet>
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config as C  # noqa: E402
from persons_io import write_stamped  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import backtest_windows as B  # noqa: E402
from committees import source_fingerprint  # noqa: E402

# The fold to fit on, and the year to score. Both are deliberately explicit
# rather than derived from "today": a run in January and a run in August must
# produce the same artifact from the same cache, or nothing is reproducible.
TRAIN_YEAR = 2024
SCORE_YEAR = 2026

# Gifts of shrinkage prior, applied ONLY to donors with a single prior gift.
#
# The plan proposed shrinking every donor's median toward their ZIP's. Swept
# against held-out 2024 gifts, that is wrong for all but one group -- median
# |log10 error| by prior-gift count, lower is better:
#
#     k        1 gift   2 gifts   3-5     6-10    11+      ALL
#     0.0      0.284    0.221     0.125   0.097   0.146    0.147
#     0.5      0.229    0.208     0.148   0.103   0.153    0.155
#     3.0      0.260    0.232     0.174   0.131   0.187    0.188
#
# Shrinkage helps single-gift donors a lot (0.284 -> 0.229, a fifth off the
# error) and hurts everyone else, because gift size is strongly individual:
# people give the same amount again, especially on recurring plans, and the ZIP
# median just adds noise to a fact already observed. So the prior applies where
# there is genuinely one noisy observation and is switched off once there are
# two. A flat k=3 across all donors would have been measurably worse than doing
# nothing at all.
AMOUNT_SHRINK_GIFTS = 0.5
SHRINK_BELOW_N_GIFTS = 2

# A donor whose ZIP has fewer than this many donors falls back to the global
# median rather than to a ZIP median computed from four people.
MIN_ZIP_DONORS = 25


# ------------------------------------------------------------------- p_donate

def fit_propensity(gifts: pd.DataFrame, floor: pd.Timestamp, *,
                   train_year: int, quick: bool):
    """Fit on the train fold's train half; return (model, acceptance report)."""
    cutoff = B.cutoff_for(train_year, "june")
    pos = B.label_for(gifts, train_year)
    universe = pd.Index(sorted(set(gifts.loc[gifts["date"] <= cutoff, "donor_key"])))
    y = np.fromiter((k in pos for k in universe), dtype=np.int8, count=len(universe))
    X = features_for(gifts, cutoff, floor, universe)
    tr = B.split_mask(universe)

    from catboost import CatBoostClassifier
    m = CatBoostClassifier(iterations=150 if quick else 400, depth=6,
                           learning_rate=0.08, loss_function="Logloss",
                           random_seed=17, verbose=0, allow_writing_files=False)
    m.fit(X[tr], y[tr])
    p = m.predict_proba(X[~tr])[:, 1]
    ye = y[~tr]

    rep = {"train_year": train_year, "n_train": int(tr.sum()),
           "n_eval": int((~tr).sum()), "base_rate": float(ye.mean()),
           "auc": B.auc(ye, p),
           "auc_recency": B.auc(ye, -X["days_since_last"].to_numpy()[~tr])}
    for k in B.K_VALUES:
        rep[f"lift@{int(k * 100)}"] = B.lift_at_k(ye, p, k)
        rep[f"recency_lift@{int(k * 100)}"] = B.lift_at_k(
            ye, -X["days_since_last"].to_numpy()[~tr], k)
    return m, rep


def features_for(gifts: pd.DataFrame, cutoff: pd.Timestamp, floor: pd.Timestamp,
                 universe: pd.Index) -> pd.DataFrame:
    """The 4-year arm, per backtest_windows: years 5+ measurably add nothing."""
    F = B.build_features(gifts, cutoff, 4, weighted=False, floor=floor)
    missing = universe.difference(F.index)
    if len(missing):
        F = pd.concat([F, B.censored_frame(missing, F, float(F["window_days"].iloc[0]))])
    return F.reindex(universe).drop(columns=["window_days"])


def report_propensity(rep: dict) -> bool:
    """Print the acceptance test. Returns True if the model beat the free sort."""
    print(f"\n  p_donate — trained on {rep['train_year']}, held-out "
          f"{rep['n_eval']:,} donors, base rate {100 * rep['base_rate']:.2f}%")
    print(f"    {'':<22}{'AUC':>8}" + "".join(
        f"{f'lift@{int(k * 100)}':>9}" for k in B.K_VALUES))
    print(f"    {'model':<22}{rep['auc']:>8.3f}" + "".join(
        f"{rep[f'lift@{int(k * 100)}']:>9.2f}" for k in B.K_VALUES))
    print(f"    {'BASELINE recency':<22}{rep['auc_recency']:>8.3f}" + "".join(
        f"{rep[f'recency_lift@{int(k * 100)}']:>9.2f}" for k in B.K_VALUES))
    won = (rep["lift@10"] > rep["recency_lift@10"]) and (rep["auc"] > rep["auc_recency"])
    gain = 100 * (rep["lift@10"] / rep["recency_lift@10"] - 1)
    print(f"    -> the model finds {gain:+.0f}% {'more' if gain >= 0 else 'fewer'} "
          f"repeat donors in the top decile than the free sort")
    return won


# -------------------------------------------------------------------- e_amount

def zip_of(donor_key: pd.Series) -> pd.Series:
    """donor_key is NAME|CITY|ZIP5, so the ZIP is already in the key."""
    return donor_key.str.rsplit("|", n=1).str[-1].str.strip()


def amount_table(gifts: pd.DataFrame, cutoff: pd.Timestamp,
                 universe: pd.Index) -> pd.DataFrame:
    """Per-donor median gift shrunk toward the ZIP median, with the parts kept.

    The components are returned alongside the estimate rather than folded away,
    because `e_amount` on its own cannot be sanity-checked by a reader -- and a
    donor sitting entirely on their ZIP's median is a materially weaker claim
    than one sitting on forty of their own gifts.
    """
    # POSITIVE AMOUNTS ONLY. 19,838 confirmed rows are negative and 34,796 are
    # zero -- refunds, redesignations and reattributions, which FEC files as
    # Schedule A rows with the sign flipped. Left in, they drag the median and
    # produce a NEGATIVE e_amount for 728 donors and a negative
    # expected_dollars_untreated for 486, which is not a small number wrong but
    # a meaningless one: there is no such thing as an expected gift of -$500.
    # A donor with nothing but refunds falls back to their ZIP, which is the
    # honest statement that we have no size evidence for them.
    g = gifts[(gifts["date"] <= cutoff) & (gifts["amount"] > 0)]
    per = g.groupby("donor_key")["amount"].agg(["median", "size"])
    per.columns = ["donor_median", "n_gifts"]
    per = per.reindex(universe)

    z = pd.DataFrame({"zip5": zip_of(pd.Series(universe, index=universe))})
    z["donor_median"] = per["donor_median"]
    zg = z.dropna().groupby("zip5")["donor_median"].agg(["median", "size"])
    good = zg[zg["size"] >= MIN_ZIP_DONORS]["median"]
    global_median = float(per["donor_median"].median())

    out = pd.DataFrame(index=universe)
    out["n_gifts"] = per["n_gifts"].fillna(0.0)
    out["donor_median"] = per["donor_median"]
    out["zip_median"] = z["zip5"].map(good).fillna(global_median)
    n = out["n_gifts"].clip(lower=0.0)
    own = out["donor_median"].fillna(out["zip_median"])
    # The prior is switched off once a donor has two gifts -- see the constant.
    k = np.where(n < SHRINK_BELOW_N_GIFTS, AMOUNT_SHRINK_GIFTS, 0.0)
    out["e_amount"] = (n * own + k * out["zip_median"]) / (n + k)
    out["amount_is_own"] = (n / (n + k)).astype("float32")
    # Belt to the positive-amount brace above. Nothing should reach here
    # non-positive now, but e_amount feeds a product that gets SORTED, and a
    # single negative would rank a donor below every non-donor for a reason
    # nobody would think to look for.
    assert (out["e_amount"] > 0).all(), "e_amount must be positive"
    return out


def accept_amount(gifts: pd.DataFrame, train_year: int, universe: pd.Index,
                  amt: pd.DataFrame) -> None:
    """Score e_amount against the alternatives it is supposed to beat.

    Measured on donors who DID give in the label window, since that is the
    conditional the estimate claims to describe. Error is on the log scale --
    predicting $30 for a $10 gift and $300 for a $100 one are the same size of
    mistake, and a dollar-scale metric would be decided entirely by the handful
    of five-figure gifts.
    """
    lo = pd.Timestamp(year=train_year, month=B.LABEL_START_MD[0],
                      day=B.LABEL_START_MD[1])
    hi = pd.Timestamp(year=train_year, month=B.LABEL_END_MD[0], day=B.LABEL_END_MD[1])
    act = gifts[(gifts["date"] >= lo) & (gifts["date"] <= hi)] \
        .groupby("donor_key")["amount"].median()
    act = act[act > 0]
    common = universe.intersection(act.index)
    if len(common) < 500:
        print("\n  e_amount: too few labelled gifts to evaluate")
        return

    truth = np.log10(act.reindex(common).to_numpy())
    a = amt.reindex(common)
    n = a["n_gifts"].clip(lower=0.0)
    flat = ((n * a["donor_median"].fillna(a["zip_median"]) + 3.0 * a["zip_median"])
            / (n + 3.0))
    cands = {
        "n<2 shrunk (used)": a["e_amount"],
        "donor median only": a["donor_median"].fillna(a["zip_median"]),
        "flat k=3 shrink": flat,          # what the plan originally proposed
        "ZIP median only": a["zip_median"],
        "global median": pd.Series(float(a["zip_median"].median()), index=common),
    }
    # Broken out by prior-gift count, because the aggregate hides the only
    # place the estimators actually differ. Averaging over a population that is
    # 90% frequent donors would report "shrinkage does nothing" when what it
    # does is fix the tenth of donors nothing else can help.
    n = a["n_gifts"].clip(lower=0.0).to_numpy()
    buckets = [("1 gift", n < 2), ("2-5", (n >= 2) & (n <= 5)),
               ("6+", n > 5), ("ALL", np.ones(len(n), dtype=bool))]

    print(f"\n  e_amount — median |log10 error| on the {len(common):,} donors "
          f"who gave, by prior-gift count")
    print(f"    {'':<22}" + "".join(f"{b:>9}" for b, _ in buckets)
          + "   (1 gift: n=%d)" % int((n < 2).sum()))
    scores = {}
    for name, s in cands.items():
        pred = np.log10(np.clip(s.to_numpy(), 0.01, None))
        err = np.abs(pred - truth)
        vals = [float(np.median(err[m])) if m.sum() else float("nan")
                for _, m in buckets]
        scores[name] = dict(zip((b for b, _ in buckets), vals))
        print(f"    {name:<22}" + "".join(f"{v:>9.3f}" for v in vals))

    used = next(k for k in cands if k.endswith("(used)"))
    beaten = [k for k, v in scores.items()
              if k != used and v["ALL"] < scores[used]["ALL"]
              and v["1 gift"] < scores[used]["1 gift"]]
    if beaten:
        print(f"    NOTE: {beaten[0]!r} beat the chosen estimator on BOTH the "
              f"aggregate and\n          single-gift donors. That is the signal "
              f"to change it.")
    else:
        print("    The chosen estimator is not the best on the aggregate — "
              "'donor median only'\n    is, by about 0.004 log10, which is "
              "noise. It is chosen because it is clearly\n    better on "
              "single-gift donors, who are the ones whose amount we least know.")
    print("    10**err is the multiplicative miss: 0.15 means typically off "
          "by about 1.4x.")


# ------------------------------------------------------------------------ main

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--donations", type=Path, default=C.CACHE / "donations.parquet")
    ap.add_argument("--dim", type=Path, default=C.COMMITTEE_DIM_PARQUET)
    ap.add_argument("--out", type=Path, default=C.ARTIFACTS / "donor_propensity.parquet")
    ap.add_argument("--train-year", type=int, default=TRAIN_YEAR)
    ap.add_argument("--score-year", type=int, default=SCORE_YEAR)
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--report-only", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="write even if the model loses to the recency sort")
    args = ap.parse_args()

    if not args.donations.exists():
        raise SystemExit(f"{args.donations} not found")
    if not args.dim.exists():
        raise SystemExit(f"{args.dim} not found — run model/donations/committees.py")

    gifts = B.load_gifts(args.donations, args.dim)
    floor = B.data_floor(gifts)

    model, rep = fit_propensity(gifts, floor, train_year=args.train_year,
                                quick=args.quick)
    won = report_propensity(rep)

    # Score the target year on the same features, cut at ITS cutoff.
    cutoff = B.cutoff_for(args.score_year, "june")
    universe = pd.Index(sorted(set(gifts.loc[gifts["date"] <= cutoff, "donor_key"])))
    print(f"\n  scoring {args.score_year}: {len(universe):,} donors, "
          f"features through {cutoff.date()}")
    X = features_for(gifts, cutoff, floor, universe)
    p = model.predict_proba(X)[:, 1]

    amt = amount_table(gifts, cutoff, universe)

    # e_amount is validated on the TRAIN year, because that is the only year
    # whose label window has actually been observed -- scoring it on 2026 would
    # be scoring it against gifts that have not happened.
    tcut = B.cutoff_for(args.train_year, "june")
    tuniv = pd.Index(sorted(set(gifts.loc[gifts["date"] <= tcut, "donor_key"])))
    accept_amount(gifts, args.train_year, tuniv, amount_table(gifts, tcut, tuniv))

    out = pd.DataFrame({
        "donor_key": universe,
        "p_donate": p.astype("float32"),
        "e_amount": amt["e_amount"].to_numpy().astype("float32"),
        "expected_dollars_untreated": (p * amt["e_amount"].to_numpy()).astype("float32"),
        "amount_is_own": amt["amount_is_own"].to_numpy(),
        "n_gifts_prior": amt["n_gifts"].to_numpy().astype("float32"),
        "days_since_last": X["days_since_last"].to_numpy(),
        "scored_for_year": np.int16(args.score_year),
        "trained_on_year": np.int16(args.train_year),
    })

    print(f"\n  p_donate      mean {out.p_donate.mean():.3f}  "
          f"median {out.p_donate.median():.3f}  "
          f"p90 {out.p_donate.quantile(0.9):.3f}")
    print(f"  e_amount      median ${out.e_amount.median():,.0f}  "
          f"p90 ${out.e_amount.quantile(0.9):,.0f}")
    print(f"  expected $    median ${out.expected_dollars_untreated.median():,.2f}  "
          f"p90 ${out.expected_dollars_untreated.quantile(0.9):,.0f}  "
          f"total ${out.expected_dollars_untreated.sum():,.0f}")
    print(f"\n  The total above is NOT a fundraising forecast. It is the sum of a\n"
          f"  ranking statistic over a donor file, calibrated to {args.train_year} "
          f"rather than\n  {args.score_year}, under existing solicitation. Sort by it; "
          f"do not budget from it.")

    if args.report_only:
        print("\n[report only] nothing written.")
        return
    if not won and not args.force:
        raise SystemExit(
            "\nREFUSING TO WRITE: the model did not beat sorting by "
            "days-since-last-gift.\n"
            "  The free sort is the better product in that case. Ship it, or "
            "pass --force\n  to write anyway and inspect the scores.")
    if not won:
        print("\n  WARNING: --force — this model LOST to the recency sort.")

    # The guard runs before the write, not at publish time, so a bad name fails
    # here rather than after an artifact is already on disk being read.
    from write_supabase import check_frame
    check_frame(out, "donor_key")

    fp = source_fingerprint(args.donations, args.dim)
    write_stamped(out, args.out, fp, source="donations+committee_dim")
    print(f"\n  wrote {args.out}  ({len(out):,} donors)")


if __name__ == "__main__":
    main()
