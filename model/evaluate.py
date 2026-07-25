"""evaluate.py — Stage F: calibrated evaluation, aggregate validation, scores.

* Scores every node with the best GTN checkpoint (cluster batches).
* Temperature-scales each head on the validation split.
* Test-split metrics + head-to-head vs the CatBoost baseline.
* ED-aggregate validation on held-out EDs (predicted vs actual rates).
* Reliability diagrams (PNG) and model/artifacts/scores.parquet
  (calibrated turnout propensity + party probabilities for ALL voters,
  including unaffiliated/BLK — the product output).

Usage:
    python model/evaluate.py [--graph PATH] [--ckpt PATH]
"""
import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import torch
import torch.nn.functional as F
from sklearn.metrics import (average_precision_score, brier_score_loss, f1_score,
                             log_loss, roc_auc_score)

import config as C
from gtn import VoterGTN
from train import CKPT, build_cluster_batches


def fit_temperature(logits: torch.Tensor, y: torch.Tensor, kind: str) -> float:
    log_t = torch.zeros(1, requires_grad=True)
    opt = torch.optim.LBFGS([log_t], lr=0.1, max_iter=60)

    def closure():
        opt.zero_grad()
        scaled = logits / log_t.exp()
        loss = (F.binary_cross_entropy_with_logits(scaled, y) if kind == "binary"
                else F.cross_entropy(scaled, y))
        loss.backward()
        return loss

    opt.step(closure)
    return float(log_t.detach().exp())


def ece(y: np.ndarray, p: np.ndarray, bins: int = 15) -> float:
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(p, edges) - 1, 0, bins - 1)
    total = 0.0
    for b in range(bins):
        m = idx == b
        if m.any():
            total += m.mean() * abs(y[m].mean() - p[m].mean())
    return float(total)


def reliability_png(y: np.ndarray, p: np.ndarray, path: Path, title: str, bins: int = 15):
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(p, edges) - 1, 0, bins - 1)
    xs, ys = [], []
    for b in range(bins):
        m = idx == b
        if m.any():
            xs.append(p[m].mean())
            ys.append(y[m].mean())
    fig, ax = plt.subplots(figsize=(4, 4))
    ax.plot([0, 1], [0, 1], "k--", lw=1)
    ax.plot(xs, ys, "o-")
    ax.set_xlabel("predicted probability")
    ax.set_ylabel("observed rate")
    ax.set_title(title)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


@torch.no_grad()
def score_all(model, batches, n_nodes: int):
    model.eval()
    t_logits = torch.zeros(n_nodes)
    p_logits = torch.zeros(n_nodes, 3)
    for b in batches:
        t, p = model(b)
        t_logits[b.node_ids] = t
        p_logits[b.node_ids] = p
    return t_logits, p_logits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--graph", type=Path, default=C.GRAPH_PT)
    ap.add_argument("--rwse", type=Path, default=None)
    ap.add_argument("--ckpt", type=Path, default=CKPT)
    ap.add_argument("--persons", type=Path, default=C.PERSONS_PARQUET)
    args = ap.parse_args()

    rwse_path = args.rwse or args.graph.with_name(args.graph.stem + "_rwse.pt")
    g = torch.load(args.graph, weights_only=False)
    rwse = torch.load(rwse_path, weights_only=False)
    ck = torch.load(args.ckpt, weights_only=False)
    model = VoterGTN(g["meta"], rwse_k=rwse.shape[1], n_edge_types=len(C.EDGE_TYPES),
                     hidden=ck["args"]["hidden"], n_layers=ck["args"]["layers"])
    model.load_state_dict(ck["model"])
    print(f"Loaded checkpoint from epoch {ck['epoch']} (val: {ck['val']})")

    batches = build_cluster_batches(g, rwse)
    n = len(g["split"])
    t_logits, p_logits = score_all(model, batches, n)
    split = g["split"].numpy()
    y_t = g["y_turnout"].numpy()
    y_p = g["y_party"].numpy()
    val, test = split == 1, split == 2
    labeled = y_p >= 0
    t_labeled = y_t >= 0            # -1 = under 18 at the target election

    # ---- temperature scaling on val ----
    val_t = torch.from_numpy(val & t_labeled)
    pv_t = torch.from_numpy(val & labeled)
    T_t = fit_temperature(t_logits[val_t], g["y_turnout"][val_t], "binary")
    T_p = fit_temperature(p_logits[pv_t], g["y_party"][pv_t], "multiclass")
    print(f"temperatures: turnout {T_t:.3f}, party {T_p:.3f}")
    t_prob = torch.sigmoid(t_logits / T_t).numpy()
    p_prob = F.softmax(p_logits / T_p, dim=1).numpy()
    t_raw = torch.sigmoid(t_logits).numpy()
    p_raw = F.softmax(p_logits, dim=1).numpy()

    metrics = {"temperature": {"turnout": T_t, "party": T_p},
               "checkpoint_epoch": ck["epoch"]}

    # ---- test metrics (turnout on eligible voters only) ----
    tst = test & t_labeled
    tt = {"auc": float(roc_auc_score(y_t[tst], t_prob[tst])),
          "pr_auc": float(average_precision_score(y_t[tst], t_prob[tst])),
          "log_loss": float(log_loss(y_t[tst], np.clip(t_prob[tst], 1e-7, 1 - 1e-7))),
          "brier": float(brier_score_loss(y_t[tst], t_prob[tst])),
          "ece_raw": ece(y_t[tst], t_raw[tst]),
          "ece_calibrated": ece(y_t[tst], t_prob[tst]),
          "n": int(tst.sum())}
    pt_mask = test & labeled
    pred = p_prob[pt_mask].argmax(1)
    pp = {"accuracy": float((pred == y_p[pt_mask]).mean()),
          "log_loss": float(log_loss(y_p[pt_mask], p_prob[pt_mask], labels=[0, 1, 2])),
          "macro_f1": float(f1_score(y_p[pt_mask], pred, average="macro")),
          "ece_dem_raw": ece((y_p[pt_mask] == 0).astype(float), p_raw[pt_mask][:, 0]),
          "ece_dem_calibrated": ece((y_p[pt_mask] == 0).astype(float), p_prob[pt_mask][:, 0]),
          "n": int(pt_mask.sum())}
    metrics["turnout_test"] = tt
    metrics["party_test"] = pp
    print(f"[turnout test] {tt}")
    print(f"[party test]   {pp}")

    # ---- head-to-head vs baseline ----
    if C.BASELINE_METRICS_JSON.exists():
        base = json.loads(C.BASELINE_METRICS_JSON.read_text())
        cmp = {
            "turnout_auc": {"catboost": base["turnout"]["test"]["auc"], "gtn": tt["auc"]},
            "turnout_log_loss": {"catboost": base["turnout"]["test"]["log_loss"],
                                 "gtn": tt["log_loss"]},
            "party_accuracy": {"catboost": base["party"]["test"]["accuracy"],
                               "gtn": pp["accuracy"]},
            "party_log_loss": {"catboost": base["party"]["test"]["log_loss"],
                               "gtn": pp["log_loss"]},
            "party_macro_f1": {"catboost": base["party"]["test"]["macro_f1"],
                               "gtn": pp["macro_f1"]},
        }
        metrics["vs_baseline"] = cmp
        print("\n=== GTN vs CatBoost (test) ===")
        for k, v in cmp.items():
            print(f"  {k:18s} catboost {v['catboost']:.4f}   gtn {v['gtn']:.4f}")

    # ---- ED-aggregate validation on held-out EDs ----
    ed_keys = pd.Series(g["ed_key"])
    df = pd.DataFrame({"ed": ed_keys, "test": test, "labeled": labeled,
                       "t_labeled": t_labeled, "y_t": y_t, "p_t": t_prob,
                       "y_dem": (y_p == 0).astype(float), "p_dem": p_prob[:, 0]})
    ed_t = (df[df["test"] & df["t_labeled"]].groupby("ed")
            .agg(pred=("p_t", "mean"), actual=("y_t", "mean"), n=("y_t", "size")))
    dl = df[df["test"] & df["labeled"]]
    ed_p = (dl.groupby("ed")
            .agg(pred=("p_dem", "mean"), actual=("y_dem", "mean"), n=("y_dem", "size")))
    ed_p = ed_p[ed_p["n"] >= 25]
    metrics["ed_aggregate"] = {
        "turnout_mae": float((ed_t["pred"] - ed_t["actual"]).abs().mean()),
        "turnout_bias": float((ed_t["pred"] - ed_t["actual"]).mean()),
        "party_dem_share_mae": float((ed_p["pred"] - ed_p["actual"]).abs().mean()),
        "party_dem_share_bias": float((ed_p["pred"] - ed_p["actual"]).mean()),
        "n_test_eds": int(len(ed_t)),
    }
    print(f"\n[ED aggregates on {len(ed_t)} held-out EDs] {metrics['ed_aggregate']}")

    reliability_png(y_t[tst], t_prob[tst], C.ARTIFACTS / "reliability_turnout.png",
                    "Turnout (test, calibrated)")
    reliability_png((y_p[pt_mask] == 0).astype(float), p_prob[pt_mask][:, 0],
                    C.ARTIFACTS / "reliability_party_dem.png",
                    "P(dem-lean) among partisans (test, calibrated)")

    # ---- product scores for every voter ----
    persons = pd.read_parquet(args.persons, columns=["person_id", "name", "party",
                                                     "ed_key", "county"])
    scores = pd.DataFrame({
        "person_id": persons["person_id"],
        "turnout_propensity": t_prob,
        "p_dem_lean": p_prob[:, 0],
        "p_rep_lean": p_prob[:, 1],
        "p_other": p_prob[:, 2],
        "registered_party": persons["party"],
        "split": split,
    })
    scores.to_parquet(C.SCORES_PARQUET, index=False)
    C.GTN_METRICS_JSON.write_text(json.dumps(metrics, indent=2))
    blk = persons["party"] == "BLK"
    print(f"\nBLK voters scored: {int(blk.sum()):,}; "
          f"mean P(dem) {scores.loc[blk, 'p_dem_lean'].mean():.3f}, "
          f"mean P(rep) {scores.loc[blk, 'p_rep_lean'].mean():.3f}")
    print(f"Wrote {C.SCORES_PARQUET} and {C.GTN_METRICS_JSON}")


if __name__ == "__main__":
    main()
