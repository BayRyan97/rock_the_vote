"""pe_rwse.py — Stage D: random-walk structural encoding, computed per cluster.

Full-graph diag(P^k) is infeasible at 2M nodes, so RWSE is exact WITHIN each
geographic training cluster and ignores cross-cluster edges (the same
approximation GraphGPS suggests for sampled subgraphs). Since training also
runs on cluster subgraphs, the PE matches what the model actually sees.

For each cluster: P = D^-1 A on the intra-cluster subgraph, then return
probabilities diag(P^1..P^K) via column-blocked sparse-dense matmuls.

Usage:
    python model/pe_rwse.py [--graph PATH] [--out PATH]
"""
import argparse
import time
from pathlib import Path

import torch

import config as C

BLOCK = 4096


def cluster_rwse(edge_index: torch.Tensor, nodes: torch.Tensor, k_steps: int) -> torch.Tensor:
    """Exact diag(P^k), k=1..k_steps, for the subgraph induced on `nodes`."""
    s = len(nodes)
    # relabel nodes to 0..s-1
    lookup = torch.full((int(nodes.max()) + 1,), -1, dtype=torch.long)
    lookup[nodes] = torch.arange(s)
    u, v = lookup[edge_index[0]], lookup[edge_index[1]]
    deg = torch.zeros(s).index_add_(0, u, torch.ones(len(u)))
    w = (1.0 / deg.clamp(min=1))[u]
    # CSR matmul is ~20x faster than COO on this box
    P = torch.sparse_coo_tensor(torch.stack([u, v]), w, (s, s)).coalesce().to_sparse_csr()

    out = torch.zeros(s, k_steps)
    for start in range(0, s, BLOCK):
        cols = torch.arange(start, min(start + BLOCK, s))
        X = torch.zeros(s, len(cols))
        X[cols, torch.arange(len(cols))] = 1.0
        for k in range(k_steps):
            X = P @ X
            out[cols, k] = X[cols, torch.arange(len(cols))]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--graph", type=Path, default=C.GRAPH_PT)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--k", type=int, default=C.RWSE_K)
    args = ap.parse_args()
    out_path = args.out or args.graph.with_name(args.graph.stem + "_rwse.pt")

    g = torch.load(args.graph, weights_only=False)
    edge_index, cluster = g["edge_index"], g["cluster"]
    n = len(cluster)
    n_clusters = int(cluster.max()) + 1
    rwse = torch.zeros(n, args.k)

    src_cluster = cluster[edge_index[0]]
    intra = src_cluster == cluster[edge_index[1]]
    ei = edge_index[:, intra]
    src_cluster = src_cluster[intra]
    print(f"{n:,} nodes, {intra.sum():,}/{len(intra):,} intra-cluster directed edges, "
          f"{n_clusters} clusters")

    order = torch.argsort(src_cluster)
    ei = ei[:, order]
    bounds = torch.searchsorted(src_cluster[order].contiguous(),
                                torch.arange(n_clusters + 1, dtype=src_cluster.dtype))
    t0 = time.time()
    for c in range(n_clusters):
        nodes = (cluster == c).nonzero(as_tuple=True)[0]
        if len(nodes) == 0:
            continue
        sub_ei = ei[:, bounds[c]:bounds[c + 1]]
        rwse[nodes] = cluster_rwse(sub_ei, nodes, args.k)
        if c % 25 == 0:
            print(f"  cluster {c}/{n_clusters} ({time.time() - t0:.0f}s)")
    assert torch.isfinite(rwse).all() and rwse.min() >= 0 and rwse.max() <= 1
    torch.save(rwse, out_path)
    print(f"Wrote {out_path} ({tuple(rwse.shape)}); "
          f"mean return prob at k=1..4: {rwse[:, :4].mean(0).tolist()}")


if __name__ == "__main__":
    main()
