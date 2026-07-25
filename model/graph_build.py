"""graph_build.py — Stage D: build the homogeneous voter graph as torch tensors.

Nodes are persons (row order of persons.parquet). Edge types, coalesced with
priority household > same_address > donation > spatial_knn > ed:

  household    clique within a household record
  same_address sampled peers across households at the same street address
  ed           sampled peers within the same election district
  spatial_knn  sampled person from each of the K nearest OTHER households
  donation     sampled co-donors to the same (non-conduit, <=5k donor) committee

Also assigns each node a geographic training cluster (~CLUSTER_TARGET_PERSONS,
whole EDs, lat/lon snake order) used for ClusterGCN-style training and RWSE.

Feature blocks follow manifest.yaml: encoder features feed the shared GPSConv
stack; party registration is packaged separately for the turnout head, tier
features separately for the party head.

Usage:
    python model/graph_build.py [--persons PATH] [--out PATH]
"""
import argparse
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import yaml
from scipy.spatial import cKDTree

import config as C
from features_history import attach_history
from splits import load_split_labels

RECENCY_FILL_DAYS = 10_000  # "never donated" sentinel, filled before standardizing

EDGE_PRIORITY = {"household": 0, "same_address": 1, "donation": 2,
                 "spatial_knn": 3, "ed": 4}
EDGE_TYPE_ID = {name: i for i, name in enumerate(C.EDGE_TYPES)}


# ------------------------------------------------------------------ edges

def sample_group_peers(groups: pd.Series, n_peers: int, rng, exclude_same: pd.Series | None = None):
    """For each node, sample n_peers others from its group (vectorized per group).

    groups: Series indexed by node id -> group key (NaN = no group)
    exclude_same: optional Series aligned with groups; peers must differ on it
    Returns (u, v) int64 arrays with u < v, deduplicated.
    """
    us, vs = [], []
    df = pd.DataFrame({"g": groups})
    if exclude_same is not None:
        df["h"] = exclude_same
    for _, idx in df.groupby("g").indices.items():
        if len(idx) < 2:
            continue
        nodes = df.index.to_numpy()[idx]
        s = len(nodes)
        cand = rng.integers(0, s, size=(s, n_peers))
        src = np.repeat(np.arange(s), n_peers)
        dst = cand.ravel()
        keep = src != dst
        if exclude_same is not None:
            hvals = df["h"].to_numpy()[idx]
            keep &= hvals[src] != hvals[dst]
        u, v = nodes[src[keep]], nodes[dst[keep]]
        us.append(np.minimum(u, v))
        vs.append(np.maximum(u, v))
    if not us:
        return np.empty(0, np.int64), np.empty(0, np.int64)
    return np.concatenate(us), np.concatenate(vs)


def household_edges(persons: pd.DataFrame, rng):
    """Full clique for real households; sampled peers for facility-sized records
    (a 649-person 'household' is an apartment building or care facility)."""
    size = persons.groupby("household_row")["household_row"].transform("size")
    small = size <= C.HOUSEHOLD_CLIQUE_CAP
    d = persons.loc[small, ["household_row"]].reset_index(names="node")
    m = d.merge(d, on="household_row")
    m = m[m["node_x"] < m["node_y"]]
    u = [m["node_x"].to_numpy(np.int64)]
    v = [m["node_y"].to_numpy(np.int64)]
    if (~small).any():
        lu, lv = sample_group_peers(persons.loc[~small, "household_row"],
                                    C.SAME_ADDRESS_PEERS, rng)
        u.append(lu)
        v.append(lv)
    return np.concatenate(u), np.concatenate(v)


def same_address_edges(persons: pd.DataFrame, rng):
    """Sampled peers among persons at the same street address but different household rows."""
    addr = (persons["county"].astype(str) + "|" + persons["city"].astype(str) + "|"
            + persons["street_name"].astype(str) + "|" + persons["address_number"].astype(str))
    multi = persons.groupby(addr)["household_row"].transform("nunique") > 1
    groups = addr.where(multi)
    return sample_group_peers(groups[multi], C.SAME_ADDRESS_PEERS, rng,
                              exclude_same=persons.loc[multi, "household_row"])


def ed_edges(persons: pd.DataFrame, rng):
    return sample_group_peers(persons["ed_key"], C.ED_PEERS_PER_VOTER, rng)


def spatial_knn_edges(persons: pd.DataFrame, rng):
    """K nearest OTHER household points; one random person per neighbor household."""
    geo = persons[persons["has_geo"] == 1]
    # unique points -> representative household lists
    pts = geo.groupby(["lon", "lat"]).indices  # (lon,lat) -> row positions within geo
    keys = np.array(list(pts.keys()))
    lon0, lat0 = keys[:, 0].mean(), keys[:, 1].mean()
    x = (keys[:, 0] - lon0) * 111_320 * np.cos(np.radians(lat0))
    y = (keys[:, 1] - lat0) * 110_540
    tree = cKDTree(np.column_stack([x, y]))
    k = C.SPATIAL_KNN_K
    _, nbr = tree.query(np.column_stack([x, y]), k=k + 1, workers=-1)
    nbr = nbr[:, 1:]  # drop self point

    geo_nodes = geo.index.to_numpy()
    point_members = [geo_nodes[np.asarray(ix)] for ix in pts.values()]
    sizes = np.array([len(m) for m in point_members])
    # for each person at point i, connect to one random person at each neighbor point
    us, vs = [], []
    flat_members = np.concatenate(point_members)
    offsets = np.concatenate([[0], np.cumsum(sizes)])
    for i, members in enumerate(point_members):
        nb_pts = nbr[i]
        # one random member of each neighbor point, for each person here
        rand_off = rng.integers(0, sizes[nb_pts], size=(len(members), k))
        partners = flat_members[offsets[nb_pts] + rand_off]
        src = np.repeat(members, k)
        dst = partners.ravel()
        us.append(np.minimum(src, dst))
        vs.append(np.maximum(src, dst))
    return np.concatenate(us), np.concatenate(vs)


def donation_edges(persons_n: int, donors: pd.DataFrame, rng):
    d = donors[["person_row", "committee"]].drop_duplicates()
    d = d[~d["committee"].str.upper().isin(C.DEM_CONDUITS | C.REP_CONDUITS)]
    sizes = d["committee"].value_counts()
    ok = sizes[(sizes >= 2) & (sizes <= C.MAX_COMMITTEE_DONORS_FOR_EDGES)].index
    d = d[d["committee"].isin(ok)]
    print(f"  donation: {d['person_row'].nunique():,} donors across {len(ok):,} committees")
    groups = pd.Series(d["committee"].to_numpy(), index=d["person_row"].to_numpy())
    # a person may appear in several committees; sample per committee group
    us, vs = [], []
    for _, grp in d.groupby("committee"):
        nodes = grp["person_row"].to_numpy(np.int64)
        s = len(nodes)
        n_p = min(C.CODONORS_PER_COMMITTEE, s - 1)
        cand = rng.integers(0, s, size=(s, n_p))
        src = np.repeat(np.arange(s), n_p)
        dst = cand.ravel()
        keep = src != dst
        u, v = nodes[src[keep]], nodes[dst[keep]]
        us.append(np.minimum(u, v))
        vs.append(np.maximum(u, v))
    if not us:
        return np.empty(0, np.int64), np.empty(0, np.int64)
    return np.concatenate(us), np.concatenate(vs)


def build_edges(persons: pd.DataFrame, donors: pd.DataFrame, rng):
    n = len(persons)
    parts = []
    for name, fn in [
        ("household", lambda: household_edges(persons, rng)),
        ("same_address", lambda: same_address_edges(persons, rng)),
        ("ed", lambda: ed_edges(persons, rng)),
        ("spatial_knn", lambda: spatial_knn_edges(persons, rng)),
        ("donation", lambda: donation_edges(n, donors, rng)),
    ]:
        u, v = fn()
        print(f"  {name}: {len(u):,} candidate pairs")
        parts.append((name, u, v))

    pair_ids = np.concatenate([u.astype(np.int64) * n + v for _, u, v in parts])
    prios = np.concatenate([np.full(len(u), EDGE_PRIORITY[name], np.int8)
                            for name, u, v in parts])
    types = np.concatenate([np.full(len(u), EDGE_TYPE_ID[name], np.int8)
                            for name, u, v in parts])
    # keep the highest-priority (lowest value) type per undirected pair
    order = np.lexsort((prios, pair_ids))
    pair_ids, prios, types = pair_ids[order], prios[order], types[order]
    first = np.ones(len(pair_ids), bool)
    first[1:] = pair_ids[1:] != pair_ids[:-1]
    pair_ids, types = pair_ids[first], types[first]
    u, v = pair_ids // n, pair_ids % n
    print(f"  {len(u):,} unique undirected edges "
          f"({np.bincount(types, minlength=len(C.EDGE_TYPES))} by type)")
    edge_index = torch.from_numpy(np.stack([np.concatenate([u, v]),
                                            np.concatenate([v, u])]))
    edge_type = torch.from_numpy(np.concatenate([types, types]))
    return edge_index, edge_type


# ---------------------------------------------------------------- clusters

def assign_clusters(persons: pd.DataFrame) -> np.ndarray:
    """Whole-ED geographic clusters of ~CLUSTER_TARGET_PERSONS via lat/lon snake order."""
    ed = persons.groupby("ed_key").agg(
        county=("county", "first"), lat=("lat", "mean"), lon=("lon", "mean"),
        n=("ed_key", "size"))
    ed["lat"] = ed["lat"].fillna(ed["lat"].mean())
    ed["lon"] = ed["lon"].fillna(ed["lon"].mean())
    cluster_ids = {}
    next_id = 0
    for county, grp in ed.groupby("county"):
        grp = grp.copy()
        grp["band"] = pd.qcut(grp["lat"], q=min(20, max(1, len(grp) // 50)),
                              labels=False, duplicates="drop")
        grp["lon_snake"] = grp["lon"] * np.where(grp["band"] % 2 == 0, 1, -1)
        grp = grp.sort_values(["band", "lon_snake"])
        cum = 0
        for key, n_p in zip(grp.index, grp["n"]):
            if cum >= C.CLUSTER_TARGET_PERSONS:
                next_id += 1
                cum = 0
            cluster_ids[key] = next_id
            cum += n_p
        next_id += 1
    out = persons["ed_key"].map(cluster_ids).to_numpy(np.int32)
    n_clusters = out.max() + 1
    sizes = np.bincount(out)
    print(f"  {n_clusters} clusters; person sizes min/median/max = "
          f"{sizes.min()}/{int(np.median(sizes))}/{sizes.max()}")
    return out


# ---------------------------------------------------------------- features

def feature_blocks(persons: pd.DataFrame, split: pd.Series):
    spec = yaml.safe_load(C.MANIFEST.read_text())["features"]
    blocks = {"encoder": {"num": [], "cat": []},
              "turnout": {"num": [], "cat": []},
              "party": {"num": [], "cat": []}}
    for name, meta in spec.items():
        if name not in persons.columns:
            print(f"  manifest feature missing from table, skipped: {name}")
            continue
        kind = "cat" if meta["type"] == "categorical" else "num"
        if "encoder" in meta["usage"]:
            blocks["encoder"][kind].append(name)
        if "turnout_head" in meta["usage"]:
            blocks["turnout"][kind].append(name)
        if "party_head" in meta["usage"]:
            blocks["party"][kind].append(name)

    train_mask = (split == "train").to_numpy()
    out, meta_out = {}, {"blocks": {k: v for k, v in blocks.items()}}

    for block, kinds in blocks.items():
        # numeric: sentinel-fill recencies, median-fill the rest, standardize on train
        num_cols = kinds["num"]
        if num_cols:
            M = persons[num_cols].astype(np.float64).copy()
            for c in num_cols:
                if c.endswith("_recency_days"):
                    M[c] = M[c].fillna(RECENCY_FILL_DAYS)
            med = M[train_mask].median()
            M = M.fillna(med)
            mean, std = M[train_mask].mean(), M[train_mask].std().replace(0, 1.0)
            M = (M - mean) / std
            out[f"{block}_num"] = torch.from_numpy(M.to_numpy(np.float32))
            meta_out[f"{block}_num_stats"] = {"cols": num_cols,
                                              "mean": mean.to_dict(), "std": std.to_dict()}
        else:
            out[f"{block}_num"] = torch.zeros(len(persons), 0)
        # categorical: integer codes, 0 = unknown/NaN
        cat_cols = kinds["cat"]
        codes, cards, cats_meta = [], [], {}
        for c in cat_cols:
            cat = pd.Categorical(persons[c].astype(str))
            codes.append(torch.from_numpy(cat.codes.astype(np.int64) + 1))
            cards.append(len(cat.categories) + 1)
            cats_meta[c] = list(cat.categories)
        out[f"{block}_cat"] = (torch.stack(codes, dim=1) if codes
                               else torch.zeros(len(persons), 0, dtype=torch.int64))
        meta_out[f"{block}_cat_cards"] = cards
        meta_out[f"{block}_cat_categories"] = cats_meta
    return out, meta_out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--persons", type=Path, default=C.PERSONS_PARQUET)
    ap.add_argument("--donors", type=Path, default=C.DONOR_COMMITTEES_PARQUET)
    ap.add_argument("--history", type=Path, default=C.HISTORY_FEATURES_PARQUET,
                    help="history_features.parquet aligned with --persons")
    ap.add_argument("--out", type=Path, default=C.GRAPH_PT)
    args = ap.parse_args()

    cols = ["household_row", "county", "town", "city", "zip_code", "street_name",
            "address_number", "ed_key", "election_district", "legislative_district",
            "congressional_district", "senate_district", "assembly_district",
            "lat", "lon", "has_geo", "age", "party", "tier_letter", "tier_count",
            "household_size", "oldest_age", "youngest_age",
            "hh_dem_share_excl", "hh_rep_share_excl", "hh_blk_share_excl",
            "max_vote_count", "min_vote_count", "engagement_gap", "num_reliable",
            "num_low_engagement", "ed_dem_share_excl", "ed_rep_share_excl",
            "ed_blk_share_excl", "ed_n_voters", "ed_mean_tier_count",
            "has_donation", "fec_n", "fec_total", "fec_recency_days",
            "nyboe_n", "nyboe_total", "nyboe_recency_days", "n_committees",
            "dem_conduit_total", "rep_conduit_total",
            "y_turnout", "y_party"]
    persons = pd.read_parquet(args.persons)
    persons = attach_history(persons, args.history)
    acs_cols = [c for c in persons.columns if c.startswith("acs_")]
    hist_cols = [c for c in persons.columns if c.startswith("hist_")]
    persons = persons[cols + acs_cols + hist_cols]
    print(f"{len(persons):,} nodes")
    rng = np.random.default_rng(C.SEED)

    donors = pd.read_parquet(args.donors)
    print("Building edges...")
    edge_index, edge_type = build_edges(persons, donors, rng)

    print("Assigning clusters...")
    cluster = assign_clusters(persons)

    print("Encoding features...")
    split = load_split_labels(persons)
    tensors, meta = feature_blocks(persons, split)

    split_id = split.map({"train": 0, "val": 1, "test": 2}).to_numpy(np.int8)
    payload = {
        "edge_index": edge_index,
        "edge_type": edge_type,
        "cluster": torch.from_numpy(cluster),
        "split": torch.from_numpy(split_id),
        "y_turnout": torch.from_numpy(persons["y_turnout"].to_numpy(np.float32)),
        "y_party": torch.from_numpy(persons["y_party"].to_numpy(np.int64)),
        "ed_key": persons["ed_key"].to_numpy(),
        "meta": meta,
        **tensors,
    }
    torch.save(payload, args.out)
    mb = args.out.stat().st_size / 1e6
    print(f"Wrote {args.out} ({mb:.0f} MB); "
          f"{edge_index.shape[1]:,} directed edges, {cluster.max() + 1} clusters")


if __name__ == "__main__":
    main()
