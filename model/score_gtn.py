#!/usr/bin/env python3
"""score_gtn.py — served GTN scores, from the SERVING-vintage graph.

evaluate.py answers "how good is this checkpoint", which needs labels: it fits
the temperature on the validation split and reports test metrics. Both are only
possible at the training vintage, where the outcome is observed.

This answers the other question — "what score does each voter get" — which needs
no labels and must use history as-of the election being predicted. It reuses the
temperature evaluate.py fitted, because there is no 2026 label to refit on.

Reuses the training graph's RWSE by default: positional encodings depend only on
edge_index and cluster, and graph structure is built from geography and donors,
never from hist_*, so the two vintages share it exactly. Verified at runtime.

Usage:
    python model/score_gtn.py                 # serving graph -> scores.parquet
    python model/score_gtn.py --graph PATH    # score some other graph
"""
import argparse
import json
from pathlib import Path

import pandas as pd
import torch
import torch.nn.functional as F

import config as C
from evaluate import score_all
from gtn import VoterGTN
from persons_io import population_fingerprint, read_stamp, write_stamped
from train import CKPT, build_cluster_batches


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--graph", type=Path, default=C.GRAPH_SERVE_PT)
    ap.add_argument("--rwse", type=Path, default=None,
                    help="default: the TRAINING graph's RWSE, which is valid "
                         "because structure does not vary with feature vintage")
    ap.add_argument("--ckpt", type=Path, default=CKPT)
    ap.add_argument("--persons", type=Path, default=C.PERSONS_PARQUET)
    ap.add_argument("--party-graph", type=Path, default=C.GRAPH_PT,
                    help="graph the PARTY head is scored from (default: the "
                         "training graph, which is where it was validated)")
    ap.add_argument("--out", type=Path, default=C.SCORES_PARQUET)
    args = ap.parse_args()

    if not args.graph.exists():
        raise SystemExit(
            f"{args.graph} not found — build it with "
            f"`python model/graph_build.py --serve`")
    if not C.GTN_METRICS_JSON.exists():
        raise SystemExit(
            f"{C.GTN_METRICS_JSON} not found — run `python model/evaluate.py` "
            f"first; its temperature is what calibrates these scores.")

    g = torch.load(args.graph, weights_only=False)
    vintage = g.get("history_target_year", C.TARGET_GENERAL_YEAR)
    if vintage == C.TARGET_GENERAL_YEAR:
        print(f"  NOTE: scoring a graph whose features are as-of "
              f"{vintage}, the TRAINING vintage. For served scores build the "
              f"serving graph with `graph_build.py --serve`.")

    # RWSE from the training graph unless told otherwise; structure is identical.
    rwse_path = args.rwse or C.GRAPH_PT.with_name(C.GRAPH_PT.stem + "_rwse.pt")
    rwse = torch.load(rwse_path, weights_only=False)
    if rwse.shape[0] != len(g["split"]):
        raise SystemExit(
            f"{rwse_path} has {rwse.shape[0]:,} rows, the graph has "
            f"{len(g['split']):,}. Rerun pe_rwse.py for this graph.")

    ck = torch.load(args.ckpt, weights_only=False)
    model = VoterGTN(g["meta"], rwse_k=rwse.shape[1], n_edge_types=len(C.EDGE_TYPES),
                     hidden=ck["args"]["hidden"], n_layers=ck["args"]["layers"])
    model.load_state_dict(ck["model"])

    temps = json.loads(C.GTN_METRICS_JSON.read_text())["temperature"]
    T_t, T_p = temps["turnout"], temps["party"]
    print(f"Checkpoint epoch {ck['epoch']}; features as-of {vintage}; "
          f"temperature turnout {T_t:.3f}, party {T_p:.3f} (from evaluate.py)")

    batches = build_cluster_batches(g, rwse)
    t_logits, _ = score_all(model, batches, len(g["split"]))
    t_prob = torch.sigmoid(t_logits / T_t).numpy()
    del batches

    # The party head is fitted and validated against the TRAINING vintage, and
    # y_party is the registration snapshot already in the file — a static label
    # that a later feature vintage cannot improve. Measured on the CatBoost head:
    # implied dem share 0.478 at the serving vintage against a true 0.521, eight
    # times the training vintage's error and enough to flip the file's aggregate
    # lean. So take party from the training graph even when serving turnout from
    # the serving one.
    if vintage != C.TARGET_GENERAL_YEAR and args.party_graph.exists():
        print(f"  party head from {args.party_graph.name} (training vintage)")
        gp = torch.load(args.party_graph, weights_only=False)
        _, p_logits = score_all(model, build_cluster_batches(gp, rwse), len(gp["split"]))
        del gp
    else:
        _, p_logits = score_all(model, build_cluster_batches(g, rwse), len(g["split"]))
    p_prob = F.softmax(p_logits / T_p, dim=1).numpy()

    persons = pd.read_parquet(args.persons,
                              columns=["person_uuid", "person_id", "party"])
    scores = pd.DataFrame({
        "person_id": persons["person_id"],
        "turnout_propensity": t_prob,
        "p_dem_lean": p_prob[:, 0],
        "p_rep_lean": p_prob[:, 1],
        "p_other": p_prob[:, 2],
        "registered_party": persons["party"],
        "split": g["split"].numpy(),
    })
    write_stamped(scores, args.out, population_fingerprint(persons),
                  history_target_year=vintage)
    print(f"Wrote {args.out} ({len(scores):,} rows, features as-of {vintage})")
    print(f"  turnout_propensity mean {t_prob.mean():.3f}  "
          f">0.7 {100 * (t_prob > 0.7).mean():.1f}%")
    nofit = (~g["turnout_fit"].numpy().astype(bool))
    print(f"  no prior ballot at this cutoff: {int(nofit.sum()):,}"
          + (f", mean {t_prob[nofit].mean():.3f}" if nofit.any() else ""))


if __name__ == "__main__":
    main()
