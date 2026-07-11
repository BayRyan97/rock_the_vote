"""baseline_catboost.py — Stage C: gradient-boosting baselines on the flat table.

Two models, features drawn strictly from manifest.yaml (leakage control):
  turnout: binary y_turnout, features tagged encoder|turnout_head
  party:   3-class y_party on registered partisans, features tagged encoder|party_head

Both use the shared ED spatial split. Metrics land in
model/artifacts/baseline_metrics.json — the bar the GTN must clear.

Usage:
    python model/baseline_catboost.py [--persons PATH] [--quick]
"""
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from catboost import CatBoostClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (average_precision_score, brier_score_loss, f1_score,
                             log_loss, roc_auc_score)

import config as C
from splits import load_split_labels


def manifest_features(task: str, available: set) -> tuple[list[str], list[str]]:
    """Return (numeric, categorical) feature lists for 'turnout' or 'party'."""
    spec = yaml.safe_load(C.MANIFEST.read_text())["features"]
    head_tag = f"{task}_head"
    numeric, categorical = [], []
    missing = []
    for name, meta in spec.items():
        if "encoder" not in meta["usage"] and head_tag not in meta["usage"]:
            continue
        if name not in available:
            missing.append(name)
            continue
        (categorical if meta["type"] == "categorical" else numeric).append(name)
    if missing:
        print(f"  [{task}] manifest features not in table (skipped): {missing}")
    return numeric, categorical


def prepare(persons: pd.DataFrame, numeric: list[str], categorical: list[str]) -> pd.DataFrame:
    X = persons[numeric + categorical].copy()
    for c in categorical:
        X[c] = X[c].astype(str).fillna("NA")
    return X


def eval_binary(y, p) -> dict:
    return {
        "auc": float(roc_auc_score(y, p)),
        "pr_auc": float(average_precision_score(y, p)),
        "log_loss": float(log_loss(y, p)),
        "brier": float(brier_score_loss(y, p)),
        "base_rate": float(np.mean(y)),
        "n": int(len(y)),
    }


def eval_multiclass(y, proba) -> dict:
    pred = proba.argmax(axis=1)
    return {
        "accuracy": float((pred == y).mean()),
        "log_loss": float(log_loss(y, proba, labels=[0, 1, 2])),
        "macro_f1": float(f1_score(y, pred, average="macro")),
        "n": int(len(y)),
    }


def train_model(X, y, cat_idx, split, quick: bool, loss: str):
    params = dict(
        loss_function=loss,
        iterations=150 if quick else 800,
        learning_rate=0.1,
        depth=6,
        early_stopping_rounds=50,
        random_seed=C.SEED,
        verbose=200,
    )
    model = CatBoostClassifier(**params)
    model.fit(X[split == "train"], y[split == "train"],
              cat_features=cat_idx,
              eval_set=(X[split == "val"], y[split == "val"]))
    return model


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--persons", type=Path, default=C.PERSONS_PARQUET)
    ap.add_argument("--quick", action="store_true", help="few iterations (smoke test)")
    args = ap.parse_args()

    persons = pd.read_parquet(args.persons)
    split = load_split_labels(persons)
    print(f"{len(persons):,} persons; splits: {split.value_counts().to_dict()}")
    available = set(persons.columns)
    metrics = {}

    # ---------------- turnout ----------------
    numeric, categorical = manifest_features("turnout", available)
    print(f"[turnout] {len(numeric)} numeric + {len(categorical)} categorical features")
    banned = {"tier_letter", "tier_count", "max_vote_count", "min_vote_count",
              "engagement_gap", "num_reliable", "num_low_engagement", "ed_mean_tier_count"}
    leaked = banned & set(numeric + categorical)
    assert not leaked, f"turnout feature set contains tier-derived features: {leaked}"

    X = prepare(persons, numeric, categorical)
    cat_idx = [X.columns.get_loc(c) for c in categorical]
    y = persons["y_turnout"].to_numpy()
    model = train_model(X, y, cat_idx, split, args.quick, "Logloss")
    metrics["turnout"] = {}
    for part in ("val", "test"):
        p = model.predict_proba(X[split == part])[:, 1]
        metrics["turnout"][part] = eval_binary(y[split == part], p)
        print(f"[turnout] {part}: {metrics['turnout'][part]}")
    imp = sorted(zip(X.columns, model.feature_importances_), key=lambda t: -t[1])[:12]
    print("[turnout] top importances:", [(n, round(v, 2)) for n, v in imp])
    model.save_model(str(C.ARTIFACTS / "baseline_turnout.cbm"))

    # age-only sanity floor
    age = persons[["age"]].to_numpy()
    lr = LogisticRegression().fit(age[split == "train"], y[split == "train"])
    p_age = lr.predict_proba(age[split == "test"])[:, 1]
    metrics["turnout"]["age_only_test_auc"] = float(roc_auc_score(y[split == "test"], p_age))
    print(f"[turnout] age-only test AUC floor: {metrics['turnout']['age_only_test_auc']:.4f}")

    # ---------------- party ----------------
    numeric, categorical = manifest_features("party", available)
    assert "party" not in numeric + categorical, "own registration leaked into party model"
    print(f"[party] {len(numeric)} numeric + {len(categorical)} categorical features")
    X = prepare(persons, numeric, categorical)
    cat_idx = [X.columns.get_loc(c) for c in categorical]
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
