"""baseline_catboost.py — Stage C: gradient-boosting baselines on the flat table.

Two models, features drawn strictly from manifest.yaml (leakage control):
  turnout: binary y_turnout, features tagged encoder|turnout_head
  party:   3-class y_party on registered partisans, features tagged encoder|party_head

Both use the shared ED spatial split. Metrics land in
baseline_metrics.json in config.ARTIFACTS — the bar the GTN must clear.

Usage:
    python model/baseline_catboost.py [--persons PATH] [--quick]
"""
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score

import config as C
from catboost_util import (PREP_CONTRACT, assert_no_cutoff_spanning, cat_indices,
                           check_no_exact_recovery, eval_binary, eval_multiclass,
                           manifest_features, party_withheld, prepare,
                           report_constant_features, train_model)
from persons_io import load_persons, read_stamp
from splits import load_split_labels


def assert_training_vintage(history_path) -> None:
    """Refuse a history file whose features postdate the label.

    features_history writes two vintages: as-of TARGET_GENERAL_YEAR for training
    and as-of SERVE_GENERAL_YEAR for scoring. They have identical columns, so
    nothing but this stamp distinguishes them — and training on the serving one
    would put the outcome inside the features.
    """
    v = read_stamp(history_path, "history_target_year")
    if v is not None and int(v) != C.TARGET_GENERAL_YEAR:
        raise SystemExit(
            f"{history_path} holds features as-of {v}, but the label is the "
            f"{C.TARGET_GENERAL_YEAR} general. Training on a later vintage is "
            f"total leakage. Use {C.HISTORY_FEATURES_PARQUET.name}.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--persons", type=Path, default=C.PERSONS_PARQUET)
    ap.add_argument("--history", type=Path, default=C.HISTORY_FEATURES_PARQUET,
                    help="history_features.parquet aligned with --persons")
    ap.add_argument("--acs", type=Path, default=C.ACS_FEATURES_PARQUET,
                    help="acs_features.parquet aligned with --persons")
    ap.add_argument("--quick", action="store_true", help="few iterations (smoke test)")
    args = ap.parse_args()

    assert_training_vintage(args.history)
    persons = load_persons(args.persons, acs_path=args.acs,
                           history_path=args.history)
    split = load_split_labels(persons)
    print(f"{len(persons):,} persons; splits: {split.value_counts().to_dict()}")
    available = set(persons.columns)
    metrics = {}

    # ---------------- turnout ----------------
    numeric, categorical = manifest_features("turnout", available)
    print(f"[turnout] {len(numeric)} numeric + {len(categorical)} categorical features")
    assert_no_cutoff_spanning("turnout", numeric, categorical)

    X = prepare(persons, numeric, categorical)
    report_constant_features(X, "turnout")
    cat_idx = cat_indices(X, categorical)
    y = persons["y_turnout"].to_numpy()
    elig = y >= 0                     # -1 = not yet 18 at the target election
    print(f"[turnout] label: voted {C.TARGET_GENERAL_YEAR} general; "
          f"{int((~elig).sum()):,} ineligible voters masked")
    Xe, ye, se = X[elig], y[elig], split[elig]

    # Selection artifact (see README): the export holds only voters with >=1
    # lifetime ballot, so at E=2024 "no pre-E ballots" nearly implies "voted E"
    # — P(voted | hist_never_voted) = 0.947 on ~149k voters. That is a property
    # of who is in the file, not of how anyone behaves, and learning it inverts
    # the predicted sign on exactly the low-propensity GOTV population. Hold the
    # cohort out of FITTING (train + the early-stopping val), but leave it in
    # test so the effect stays measurable rather than hidden.
    never_e = persons.loc[elig, "hist_never_voted"].to_numpy() == 1
    se_fit = se.copy()
    se_fit[never_e & se_fit.isin(["train", "val"])] = "excl"
    n_excl = int((se_fit == "excl").sum())
    print(f"[turnout] never-voter cohort held out of fitting: {n_excl:,} "
          f"({never_e.mean():.1%} of eligible; base rate "
          f"{ye[never_e].mean():.3f} vs {ye[~never_e].mean():.3f} for the rest)")

    model = train_model(Xe, ye, cat_idx, se_fit, args.quick, "Logloss")
    metrics["turnout"] = {"target_general_year": C.TARGET_GENERAL_YEAR,
                          "n_masked_ineligible": int((~elig).sum()),
                          "n_never_voter_excluded_from_fit": n_excl}
    for part in ("val", "test"):
        p = model.predict_proba(Xe[se == part])[:, 1]
        metrics["turnout"][part] = eval_binary(ye[se == part], p)
        print(f"[turnout] {part}: {metrics['turnout'][part]}")

    # Report the cohort separately on test: full-population metrics average
    # over it and hide whether the inverted sign actually went away.
    tst = (se == "test").to_numpy()
    p_tst = model.predict_proba(Xe[tst])[:, 1]
    for tag, m in (("test_never_voters", never_e[tst]),
                   ("test_excl_never_voters", ~never_e[tst])):
        if m.sum() > 0 and len(np.unique(ye[tst][m])) > 1:
            metrics["turnout"][tag] = eval_binary(ye[tst][m], p_tst[m])
            metrics["turnout"][tag]["mean_pred"] = float(p_tst[m].mean())
            print(f"[turnout] {tag}: {metrics['turnout'][tag]}")
    imp = sorted(zip(X.columns, model.feature_importances_), key=lambda t: -t[1])[:12]
    print("[turnout] top importances:", [(n, round(v, 2)) for n, v in imp])
    model.get_metadata()["prep_contract"] = PREP_CONTRACT
    model.save_model(str(C.ARTIFACTS / "baseline_turnout.cbm"))

    # age-only sanity floor
    age = persons.loc[elig, ["age"]].to_numpy()
    lr = LogisticRegression().fit(age[se_fit == "train"], ye[se_fit == "train"])
    p_age = lr.predict_proba(age[se == "test"])[:, 1]
    metrics["turnout"]["age_only_test_auc"] = float(roc_auc_score(ye[se == "test"], p_age))
    print(f"[turnout] age-only test AUC floor: {metrics['turnout']['age_only_test_auc']:.4f}")

    # ---------------- party ----------------
    numeric, categorical = manifest_features("party", available)
    assert "party" not in numeric + categorical, "own registration leaked into party model"
    print(f"[party] {len(numeric)} numeric + {len(categorical)} categorical features")
    # Withholding is only real if the withheld feature cannot be rebuilt from
    # what the head does see (hist_n_votes = n_generals + n_primaries was).
    check_no_exact_recovery(persons, numeric,
                            party_withheld(numeric, categorical, available), "party")
    X = prepare(persons, numeric, categorical)
    report_constant_features(X, "party")
    cat_idx = cat_indices(X, categorical)
    y = persons["y_party"].to_numpy()
    labeled = y != C.PARTY_MASKED
    Xl, yl, sl = X[labeled], y[labeled], split[labeled]
    model = train_model(Xl, yl, cat_idx, sl, args.quick, "MultiClass")
    metrics["party"] = {}
    for part in ("val", "test"):
        proba = model.predict_proba(Xl[sl == part])
        metrics["party"][part] = eval_multiclass(yl[sl == part], proba)
        print(f"[party] {part}: {metrics['party'][part]}")
    imp = sorted(zip(X.columns, model.feature_importances_), key=lambda t: -t[1])[:12]
    print("[party] top importances:", [(n, round(v, 2)) for n, v in imp])
    model.get_metadata()["prep_contract"] = PREP_CONTRACT
    model.save_model(str(C.ARTIFACTS / "baseline_party.cbm"))

    # score the unaffiliated (the product output for the party task)
    blk = ~labeled
    if blk.any():
        proba_blk = model.predict_proba(X[blk])
        metrics["party"]["blk_scored"] = {
            "n": int(blk.sum()),
            "mean_proba": [float(x) for x in proba_blk.mean(axis=0)],
        }
        print(f"[party] BLK voters scored: n={blk.sum():,}, "
              f"mean class proba={np.round(proba_blk.mean(axis=0), 3)}")

    C.BASELINE_METRICS_JSON.write_text(json.dumps(metrics, indent=2))
    print(f"Wrote {C.BASELINE_METRICS_JSON}")


if __name__ == "__main__":
    main()
