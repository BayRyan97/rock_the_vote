"""backtest_temporal.py — Stage C2: cross-election turnout backtest.

The product question is "who votes in the NEXT election?", so the honest test
is temporal: fit turnout on general E_train (features as-of E_train, label
"voted the E_train general"), then predict general E_test with features
as-of E_test, evaluated on the spatially held-out test EDs. A same-year
reference model (trained on E_test) isolates the temporal-transfer gap.

Feature set: hist_* with hist_age_at_target in place of raw age, household
ages de-aged per cutoff, household structure, geography, ACS, and
registration-derived features. Donation features are excluded — etl.py
computes them as-of config.TARGET_GENERAL_YEAR only, which would leak into an
earlier cutoff. Registration (party + shares) is the export-date snapshot for
BOTH years; it moves slowly, accepted caveat.

Roll caveat: the voter file is one export-date snapshot. Movers-in lack
pre-arrival history, movers-out are absent entirely, and both cutoffs inherit
this equally — treat absolute numbers as optimistic for older E_train.

Usage:
    python model/backtest_temporal.py [--train-year 2020] [--test-year 2024]
                                      [--quick]
"""
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from catboost import CatBoostClassifier
from sklearn.metrics import log_loss, roc_auc_score

import config as C
from catboost_util import as_category, manifest_spec
from features_history import REF_YEAR, build_features
from persons_io import attach_acs
from splits import load_split_labels

DEAGE_COLS = ["oldest_age", "youngest_age"]   # export-date ages, shifted per cutoff
DONATION_COLS = {"has_donation", "fec_n", "fec_total", "fec_recency_days",
                 "nyboe_n", "nyboe_total", "nyboe_recency_days",
                 "n_committees", "dem_conduit_total", "rep_conduit_total"}


def persons_feature_lists(available: set) -> tuple[list[str], list[str]]:
    """Manifest turnout features that are usable at ANY cutoff (persons side).

    Deliberately NOT catboost_util.manifest_features: this selector also drops
    donation features (as-of the config target only), raw age (replaced by
    hist_age_at_target) and every hist_* column, because the backtest rebuilds
    history per candidate cutoff rather than reusing the shipped one.
    """
    spec = manifest_spec()
    numeric, categorical = [], []
    for name, meta in spec.items():
        if "encoder" not in meta["usage"] and "turnout_head" not in meta["usage"]:
            continue
        if (meta.get("spans_cutoff") or name in DONATION_COLS or name == "age"
                or name.startswith("hist_") or name not in available):
            continue
        (categorical if meta["type"] == "categorical" else numeric).append(name)
    return numeric, categorical


def design_matrix(persons: pd.DataFrame, hist: pd.DataFrame, E: int,
                  numeric: list[str], categorical: list[str]) -> pd.DataFrame:
    hist_cols = [c for c in hist.columns
                 if c.startswith("hist_")]      # includes hist_age_at_target
    X = pd.concat([persons[numeric + categorical].reset_index(drop=True),
                   hist[hist_cols]], axis=1)
    shift = REF_YEAR - E
    for c in DEAGE_COLS:
        X[c] = X[c] - shift
    spec = manifest_spec()
    for c in categorical:
        # Shared renderer, so a level here means the same thing as in the
        # baseline this backtest exists to be compared with. The local copy
        # this replaces had the dead-fillna bug: astype(str) stringifies
        # missing first, so the "NA" sentinel never landed.
        X[c] = as_category(X[c], spec.get(c, {}).get("format", "string"), c)
    return X


def fit(X, y, split, cat_idx, quick: bool):
    model = CatBoostClassifier(
        loss_function="Logloss",
        iterations=150 if quick else 800,
        learning_rate=0.1, depth=6, early_stopping_rounds=50,
        random_seed=C.SEED, verbose=200,
    )
    # positional masks: X may carry a non-contiguous index after row filtering
    tr = (split == "train").to_numpy()
    va = (split == "val").to_numpy()
    model.fit(X[tr], y[tr], cat_features=cat_idx, eval_set=(X[va], y[va]))
    return model


def fit_labels(split, elig, never):
    """Split labels with the never-voter cohort held out of FITTING only.

    Mirrors baseline_catboost.py: the cohort stays in test so the effect
    remains measurable, and leaves train/val so the model cannot learn that
    "no prior ballots" implies "voted" — a property of who is in the export,
    not of anyone's behaviour.
    """
    s = split[elig].reset_index(drop=True).copy()
    s[never[elig] & s.isin(["train", "val"])] = "excl"
    return s


def test_metrics(model, X, y, split, ed_keys, never=None) -> dict:
    te = (split == "test").to_numpy()
    p = model.predict_proba(X[te])[:, 1]
    ed = pd.DataFrame({"ed": ed_keys[te], "y": y[te], "p": p}).groupby("ed").agg(
        pred=("p", "mean"), actual=("y", "mean"))
    out = {
        "auc": float(roc_auc_score(y[te], p)),
        "log_loss": float(log_loss(y[te], np.clip(p, 1e-7, 1 - 1e-7))),
        "base_rate": float(y[te].mean()),
        "ed_turnout_mae": float((ed["pred"] - ed["actual"]).abs().mean()),
        "ed_turnout_bias": float((ed["pred"] - ed["actual"]).mean()),
        "n": int(te.sum()),
        "n_test_eds": int(len(ed)),
    }
    # baseline_metrics.json reports turnout.test (blended) and
    # turnout.test_excl_never_voters. Emit both here or the two files still
    # cannot be read against each other.
    if never is not None:
        m = ~never[te]
        if m.sum() and len(np.unique(y[te][m])) > 1:
            out["auc_excl_never_voters"] = float(roc_auc_score(y[te][m], p[m]))
            out["n_excl_never_voters"] = int(m.sum())
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--train-year", type=int, default=2020)
    ap.add_argument("--test-year", type=int, default=C.TARGET_GENERAL_YEAR)
    ap.add_argument("--persons", type=Path, default=C.PERSONS_PARQUET)
    ap.add_argument("--elections", type=Path, default=C.ELECTIONS_PARQUET)
    ap.add_argument("--quick", action="store_true")
    args = ap.parse_args()
    E_tr, E_te = args.train_year, args.test_year

    # ACS lives in its own file now; the backtest builds history per cutoff
    # itself, so it joins ACS only.
    persons = attach_acs(pd.read_parquet(args.persons))
    elections = pd.read_parquet(args.elections)
    split = load_split_labels(persons)
    ed_keys = persons["ed_key"].to_numpy()
    age = persons["age"].to_numpy(np.float64)
    print(f"{len(persons):,} persons; backtest train={E_tr} general -> test={E_te} general")

    numeric, categorical = persons_feature_lists(set(persons.columns))
    frames = {}
    for E in {E_tr, E_te}:
        hist = build_features(persons, elections, E)
        frames[E] = {
            "X": design_matrix(persons, hist, E, numeric, categorical),
            "y": hist[f"y_voted_general_{E}"].to_numpy(),
            "elig": (age - (REF_YEAR - E)) >= 18,
            # Same hold-out as baseline_catboost/graph_build/train/evaluate, but
            # computed as-of THIS cutoff rather than the shipped 2024 one.
            "never": hist["hist_never_voted"].to_numpy() == 1,
        }
        print(f"  as-of {E}: voted mean {frames[E]['y'][frames[E]['elig']].mean():.3f} "
              f"among {int(frames[E]['elig'].sum()):,} eligible")
    cat_idx = [frames[E_te]["X"].columns.get_loc(c) for c in categorical]

    results = {"train_year": E_tr, "test_year": E_te,
               "n_features": frames[E_te]["X"].shape[1]}
    # temporal model: fit on E_train, evaluate predicting E_test
    f_tr, f_te = frames[E_tr], frames[E_te]
    s_tr = fit_labels(split, f_tr["elig"], f_tr["never"])
    print(f"  never-voter cohort held out of the {E_tr} fit: "
          f"{int((s_tr == 'excl').sum()):,}")
    m_temporal = fit(f_tr["X"][f_tr["elig"]], f_tr["y"][f_tr["elig"]],
                     s_tr, cat_idx, args.quick)
    results["temporal"] = test_metrics(
        m_temporal, f_te["X"][f_te["elig"]].reset_index(drop=True),
        f_te["y"][f_te["elig"]], split[f_te["elig"]].reset_index(drop=True),
        ed_keys[f_te["elig"]], f_te["never"][f_te["elig"]])
    print(f"[temporal {E_tr}->{E_te}] {results['temporal']}")
    if E_tr != E_te:
        del f_tr["X"]                        # free ~2 GB before the second fit

    # same-year reference: fit and evaluate on E_test
    s_te = fit_labels(split, f_te["elig"], f_te["never"])
    print(f"  never-voter cohort held out of the {E_te} fit: "
          f"{int((s_te == 'excl').sum()):,}")
    m_ref = fit(f_te["X"][f_te["elig"]], f_te["y"][f_te["elig"]],
                s_te, cat_idx, args.quick)
    results["same_year_reference"] = test_metrics(
        m_ref, f_te["X"][f_te["elig"]].reset_index(drop=True),
        f_te["y"][f_te["elig"]], split[f_te["elig"]].reset_index(drop=True),
        ed_keys[f_te["elig"]], f_te["never"][f_te["elig"]])
    print(f"[reference {E_te}->{E_te}] {results['same_year_reference']}")

    gap = results["same_year_reference"]["auc"] - results["temporal"]["auc"]
    results["temporal_auc_gap"] = float(gap)
    print(f"temporal transfer gap (reference AUC - temporal AUC): {gap:+.4f}")

    imp = sorted(zip(f_te["X"].columns, m_temporal.feature_importances_),
                 key=lambda t: -t[1])[:10]
    results["temporal_top_importances"] = [(n, round(float(v), 2)) for n, v in imp]
    print("temporal top importances:", results["temporal_top_importances"])

    out = C.ARTIFACTS / f"backtest_{E_tr}to{E_te}.json"
    out.write_text(json.dumps(results, indent=2))
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
