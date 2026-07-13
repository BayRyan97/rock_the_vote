"""train.py — Stage E: ClusterGCN-style training of the VoterGTN.

Each training batch is one geographic cluster's induced subgraph (whole EDs,
~8k nodes) — no compiled sampling extensions needed, which matters on this
Windows-on-ARM box where pyg-lib/torch-sparse aren't available. Loss is
computed on train-split nodes only; val nodes contribute context, never
gradient (their labels are never model inputs).

Usage:
    python model/train.py [--graph PATH] [--epochs 30] [--quick]
"""
import argparse
import json
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from torch_geometric.data import Data

import config as C
from gtn import VoterGTN

CKPT = C.ARTIFACTS / "gtn_best.pt"


def build_cluster_batches(g: dict, rwse: torch.Tensor) -> list[Data]:
    """Pre-slice the graph into per-cluster Data objects (fits in RAM)."""
    cluster = g["cluster"]
    edge_index, edge_type = g["edge_index"], g["edge_type"]
    intra = cluster[edge_index[0]] == cluster[edge_index[1]]
    ei, et = edge_index[:, intra], edge_type[intra]
    src_cluster = cluster[ei[0]]
    order = torch.argsort(src_cluster)
    ei, et = ei[:, order], et[order]
    n_clusters = int(cluster.max()) + 1
    bounds = torch.searchsorted(src_cluster[order].contiguous(),
                                torch.arange(n_clusters + 1, dtype=src_cluster.dtype))
    batches = []
    for c in range(n_clusters):
        nodes = (cluster == c).nonzero(as_tuple=True)[0]
        if len(nodes) == 0:
            continue
        lookup = torch.full((int(nodes.max()) + 1,), -1, dtype=torch.long)
        lookup[nodes] = torch.arange(len(nodes))
        sub_ei = ei[:, bounds[c]:bounds[c + 1]]
        d = Data(
            edge_index=lookup[sub_ei],
            edge_type=et[bounds[c]:bounds[c + 1]],
            enc_num=g["encoder_num"][nodes], enc_cat=g["encoder_cat"][nodes],
            turnout_num=g["turnout_num"][nodes], turnout_cat=g["turnout_cat"][nodes],
            party_num=g["party_num"][nodes], party_cat=g["party_cat"][nodes],
            rwse=rwse[nodes],
            y_turnout=g["y_turnout"][nodes], y_party=g["y_party"][nodes],
            split=g["split"][nodes], node_ids=nodes,
            num_nodes=len(nodes),
        )
        batches.append(d)
    return batches


def multitask_loss(t_logit, p_logit, batch, mask, w_t=1.0, w_p=1.0):
    losses, parts = 0.0, {}
    if mask.any():
        l_t = F.binary_cross_entropy_with_logits(t_logit[mask], batch.y_turnout[mask])
        losses = losses + w_t * l_t
        parts["turnout"] = float(l_t.detach())
    p_mask = mask & (batch.y_party >= 0)
    if p_mask.any():
        l_p = F.cross_entropy(p_logit[p_mask], batch.y_party[p_mask])
        losses = losses + w_p * l_p
        parts["party"] = float(l_p.detach())
    return losses, parts


@torch.no_grad()
def evaluate(model, batches, split_id: int) -> dict:
    from sklearn.metrics import f1_score, log_loss, roc_auc_score
    model.eval()
    t_probs, t_ys, p_probs, p_ys = [], [], [], []
    for b in batches:
        m = b.split == split_id
        if not m.any():
            continue
        t_logit, p_logit = model(b)
        t_probs.append(torch.sigmoid(t_logit[m]))
        t_ys.append(b.y_turnout[m])
        pm = m & (b.y_party >= 0)
        if pm.any():
            p_probs.append(F.softmax(p_logit[pm], dim=1))
            p_ys.append(b.y_party[pm])
    t_p = torch.cat(t_probs).numpy()
    t_y = torch.cat(t_ys).numpy()
    p_p = torch.cat(p_probs).numpy()
    p_y = torch.cat(p_ys).numpy()
    return {
        "turnout_auc": float(roc_auc_score(t_y, t_p)),
        "turnout_log_loss": float(log_loss(t_y, np.clip(t_p, 1e-7, 1 - 1e-7))),
        "party_acc": float((p_p.argmax(1) == p_y).mean()),
        "party_log_loss": float(log_loss(p_y, p_p, labels=[0, 1, 2])),
        "party_macro_f1": float(f1_score(p_y, p_p.argmax(1), average="macro")),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--graph", type=Path, default=C.GRAPH_PT)
    ap.add_argument("--rwse", type=Path, default=None)
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--hidden", type=int, default=128)
    ap.add_argument("--layers", type=int, default=3)
    ap.add_argument("--patience", type=int, default=5)
    ap.add_argument("--quick", action="store_true",
                    help="tiny model + first 10 clusters (smoke test)")
    args = ap.parse_args()
    torch.manual_seed(C.SEED)

    rwse_path = args.rwse or args.graph.with_name(args.graph.stem + "_rwse.pt")
    print(f"Loading {args.graph} + {rwse_path}...")
    g = torch.load(args.graph, weights_only=False)
    rwse = torch.load(rwse_path, weights_only=False)
    batches = build_cluster_batches(g, rwse)
    if args.quick:
        batches = batches[:10]
        args.epochs, args.hidden, args.layers = min(args.epochs, 2), 32, 2
    print(f"{len(batches)} cluster batches")

    model = VoterGTN(g["meta"], rwse_k=rwse.shape[1], n_edge_types=len(C.EDGE_TYPES),
                     hidden=args.hidden, n_layers=args.layers)
    n_params = sum(p.numel() for p in model.parameters())
    print(f"VoterGTN: {n_params:,} params, hidden={args.hidden}, layers={args.layers}")
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-5)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=args.epochs)

    best_val, best_epoch, history = float("inf"), -1, []
    rng = np.random.default_rng(C.SEED)
    for epoch in range(args.epochs):
        model.train()
        t0 = time.time()
        order = rng.permutation(len(batches))
        tot, n_seen = 0.0, 0
        for i in order:
            b = batches[i]
            mask = b.split == 0
            if not mask.any():
                continue
            opt.zero_grad()
            t_logit, p_logit = model(b)
            loss, _ = multitask_loss(t_logit, p_logit, b, mask)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            tot += float(loss) * int(mask.sum())
            n_seen += int(mask.sum())
        sched.step()
        val = evaluate(model, batches, split_id=1)
        val_loss = val["turnout_log_loss"] + val["party_log_loss"]
        history.append({"epoch": epoch, "train_loss": tot / max(n_seen, 1),
                        **val, "sec": round(time.time() - t0, 1)})
        print(f"epoch {epoch:2d} | train {tot / max(n_seen, 1):.4f} | "
              f"val turnout AUC {val['turnout_auc']:.4f} ll {val['turnout_log_loss']:.4f} | "
              f"party acc {val['party_acc']:.4f} ll {val['party_log_loss']:.4f} | "
              f"{time.time() - t0:.0f}s")
        if val_loss < best_val - 1e-4:
            best_val, best_epoch = val_loss, epoch
            torch.save({"model": model.state_dict(), "args": vars(args),
                        "epoch": epoch, "val": val}, CKPT)
        elif epoch - best_epoch >= args.patience:
            print(f"early stop (best epoch {best_epoch})")
            break
    (C.ARTIFACTS / "train_history.json").write_text(json.dumps(history, indent=2))
    print(f"Best val loss {best_val:.4f} @ epoch {best_epoch}; checkpoint {CKPT}")


if __name__ == "__main__":
    main()
