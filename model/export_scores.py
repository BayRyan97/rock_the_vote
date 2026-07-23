"""export_scores.py — Stage G: score every voter with the CatBoost baselines
and emit the front-end product files.

The GTN's evaluate.py writes model/artifacts/scores.parquet from the graph
model; this script writes the SAME schema from the gradient-boosting baselines
(baseline_turnout.cbm + baseline_party.cbm). It exists because the CatBoost
turnout model is the recommended production scorer (see model/README + notes),
it needs no 4.5 h graph build, and it re-runs in minutes after any data
refresh. Whichever stage last writes scores.parquet wins; build/build.py reads
the compact derivative, so both paths are interchangeable.

Outputs:
  model/artifacts/scores.parquet   person_id + calibrated turnout propensity +
                                   party probabilities for ALL voters (incl.
                                   unaffiliated/BLK — the product output).
  data/model_scores.parquet        compact, GitHub-committed build input:
                                   one row per (person_id, registered party)
                                   with turnout / p_dem / p_rep as 0-100 uint8.
  data/model_importances.json      global feature importances + headline
                                   accuracy/calibration for the front-end
                                   "what drives the model" panel.

Usage:
    python model/export_scores.py [--persons PATH] [--history PATH] [--quick]
"""
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from catboost import CatBoostClassifier
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import roc_auc_score, brier_score_loss

import config as C
from baseline_catboost import manifest_features, prepare
from features_history import attach_history
from splits import load_split_labels

TOP_FEATURES = 12          # importances surfaced per task in the UI panel


def _ece(y: np.ndarray, p: np.ndarray, bins: int = 15) -> float:
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(p, edges) - 1, 0, bins - 1)
    total = 0.0
    for b in range(bins):
        m = idx == b
        if m.any():
            total += m.mean() * abs(y[m].mean() - p[m].mean())
    return float(total)


def _score(model: CatBoostClassifier, X: pd.DataFrame) -> np.ndarray:
    """predict_proba with columns reordered to the model's training order."""
    return model.predict_proba(X[list(model.feature_names_)])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--persons", type=Path, default=C.PERSONS_PARQUET)
    ap.add_argument("--history", type=Path, default=C.HISTORY_FEATURES_PARQUET)
    ap.add_argument("--scores-out", type=Path, default=C.SCORES_PARQUET)
    ap.add_argument("--compact-out", type=Path, default=C.DATA / "model_scores.parquet")
    ap.add_argument("--importances-out", type=Path,
                    default=C.DATA / "model_importances.json")
    args = ap.parse_args()

    persons = pd.read_parquet(args.persons)
    persons = attach_history(persons, args.history)
    split = load_split_labels(persons).to_numpy()
    available = set(persons.columns)
    print(f"{len(persons):,} voters; splits {pd.Series(split).value_counts().to_dict()}")

    turnout_model = CatBoostClassifier().load_model(str(C.ARTIFACTS / "baseline_turnout.cbm"))
    party_model = CatBoostClassifier().load_model(str(C.ARTIFACTS / "baseline_party.cbm"))

    # ---- turnout: score everyone, isotonic-calibrate on the val split -------
    t_num, t_cat = manifest_features("turnout", available)
    Xt = prepare(persons, t_num, t_cat)
    t_raw = _score(turnout_model, Xt)[:, 1]

    y_t = persons["y_turnout"].to_numpy()
    elig = y_t >= 0                                  # -1 = under 18 at target E
    val = (split == "val") & elig
    iso = IsotonicRegression(out_of_bounds="clip").fit(t_raw[val], y_t[val])
    t_cal = iso.predict(t_raw).astype(np.float32)

    test = (split == "test") & elig
    print(f"[turnout] test AUC {roc_auc_score(y_t[test], t_cal[test]):.4f} "
          f"Brier {brier_score_loss(y_t[test], t_cal[test]):.4f} "
          f"ECE {_ece(y_t[test], t_raw[test]):.4f}->{_ece(y_t[test], t_cal[test]):.4f} "
          f"(base rate {y_t[test].mean():.3f})")

    # ---- party: 3-class probs for everyone (BLK included) -------------------
    p_num, p_cat = manifest_features("party", available)
    Xp = prepare(persons, p_num, p_cat)
    p_prob = _score(party_model, Xp)                 # cols: dem_lean, rep_lean, other

    # ---- full scores.parquet (drop-in for evaluate.py's output) -------------
    scores = pd.DataFrame({
        "person_id": persons["person_id"].to_numpy(),
        "turnout_propensity": t_cal,
        "p_dem_lean": p_prob[:, 0].astype(np.float32),
        "p_rep_lean": p_prob[:, 1].astype(np.float32),
        "p_other": p_prob[:, 2].astype(np.float32),
        "registered_party": persons["party"].to_numpy(),
        "split": np.where(split == "train", 0, np.where(split == "val", 1, 2)).astype(np.int8),
    })
    scores.to_parquet(args.scores_out, index=False)
    blk = persons["party"] == "BLK"
    print(f"Wrote {args.scores_out}: {len(scores):,} voters; "
          f"BLK ({int(blk.sum()):,}) mean P(dem) {scores.loc[blk.to_numpy(), 'p_dem_lean'].mean():.3f} "
          f"P(rep) {scores.loc[blk.to_numpy(), 'p_rep_lean'].mean():.3f}")

    # ---- compact committed build input: quantise to 0-100 uint8 -------------
    # Collapse genuine (person_id, party) collisions (Jr/Sr, same registration)
    # by mean — they are indistinguishable from the front-end household key.
    q = pd.DataFrame({
        "person_id": scores["person_id"],
        "party": scores["registered_party"],
        "t": np.rint(np.clip(scores["turnout_propensity"], 0, 1) * 100),
        "d": np.rint(np.clip(scores["p_dem_lean"], 0, 1) * 100),
        "r": np.rint(np.clip(scores["p_rep_lean"], 0, 1) * 100),
    })
    compact = (q.groupby(["person_id", "party"], as_index=False)[["t", "d", "r"]]
               .mean().round())
    for c in ("t", "d", "r"):
        compact[c] = compact[c].clip(0, 100).astype(np.uint8)
    compact.to_parquet(args.compact_out, index=False)
    size_mb = args.compact_out.stat().st_size / 1024 / 1024
    print(f"Wrote {args.compact_out}: {len(compact):,} rows ({size_mb:.1f} MB, "
          f"{scores['person_id'].duplicated().sum():,} collisions folded)")

    # ---- global feature importances + headline metrics for the UI panel -----
    def top(model):
        pairs = sorted(zip(model.feature_names_, model.get_feature_importance()),
                       key=lambda kv: -kv[1])[:TOP_FEATURES]
        return [[n, round(float(v), 2)] for n, v in pairs]

    base = json.loads(C.BASELINE_METRICS_JSON.read_text()) if C.BASELINE_METRICS_JSON.exists() else {}
    importances = {
        "target_general_year": C.TARGET_GENERAL_YEAR,
        "n_voters": int(len(scores)),
        "turnout": {
            "importances": top(turnout_model),
            "test_auc": round(float(roc_auc_score(y_t[test], t_cal[test])), 4),
            "test_brier": round(float(brier_score_loss(y_t[test], t_cal[test])), 4),
            "base_rate": round(float(y_t[test].mean()), 4),
        },
        "party": {
            "importances": top(party_model),
            "test_accuracy": round(float(base.get("party", {}).get("test", {}).get("accuracy", 0)), 4),
            "test_macro_f1": round(float(base.get("party", {}).get("test", {}).get("macro_f1", 0)), 4),
        },
    }
    args.importances_out.write_text(json.dumps(importances, indent=2))
    print(f"Wrote {args.importances_out}")


if __name__ == "__main__":
    main()
