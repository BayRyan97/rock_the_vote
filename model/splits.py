"""splits.py — spatial holdout by whole election districts, shared by the
CatBoost baseline and the GTN.

Random node splits leak through household and geographic edges, so entire EDs
are assigned to train/val/test (research doc §5). Assignment is deterministic
(seeded), stratified by county, and balanced on person counts.

Usage:
    python model/splits.py [--persons PATH] [--out PATH]

As a library:
    from splits import assign_splits
"""
import argparse
from pathlib import Path

import numpy as np
import pandas as pd

import config as C

VALID_SPLITS = ("train", "val", "test")


def assign_splits(persons: pd.DataFrame, seed: int = C.SEED,
                  fracs: dict = C.SPLIT_FRACS) -> pd.DataFrame:
    """Return DataFrame (ed_key, county, n_persons, split)."""
    eds = (persons.groupby(["ed_key", "county"], as_index=False)
           .size().rename(columns={"size": "n_persons"}))
    rng = np.random.default_rng(seed)
    parts = []
    for county, grp in eds.groupby("county"):
        grp = grp.sample(frac=1.0, random_state=rng.integers(0, 2**31 - 1)).reset_index(drop=True)
        # Assign EDs in shuffled order until each split's person budget fills.
        total = grp["n_persons"].sum()
        cum = grp["n_persons"].cumsum()
        train_cut = fracs["train"] * total
        val_cut = (fracs["train"] + fracs["val"]) * total
        grp["split"] = np.where(cum <= train_cut, "train",
                        np.where(cum <= val_cut, "val", "test"))
        parts.append(grp)
    out = pd.concat(parts, ignore_index=True)
    counts = (out.groupby("split")["n_persons"].sum() / out["n_persons"].sum()).round(3)
    print(f"  split shares (persons): {counts.to_dict()}   EDs: {out.groupby('split').size().to_dict()}")
    return out


def _split_lookup(splits: pd.DataFrame, path: Path) -> pd.Series:
    """Validate the split table itself, before anyone maps through it.

    Checking here rather than after the map means a bad label is reported
    against the file that contains it, and means the mapped result cannot hold
    anything outside VALID_SPLITS.
    """
    if splits["ed_key"].isna().any():
        raise ValueError(
            f"{path} has {int(splits['ed_key'].isna().sum()):,} row(s) with a "
            f"null ed_key. Rerun `python model/splits.py`.")
    dupes = splits["ed_key"].duplicated()
    if dupes.any():
        ex = splits.loc[dupes, "ed_key"].astype(str).drop_duplicates().head(3).tolist()
        raise ValueError(
            f"{path} assigns {int(dupes.sum()):,} ed_key(s) more than once, e.g. "
            f"{ex} — one ED cannot be in two splits. Rerun `python model/splits.py`.")
    bad = splits.loc[~splits["split"].isin(VALID_SPLITS), "split"]
    if len(bad):
        ex = sorted(bad.astype(str).drop_duplicates().head(3).tolist())
        raise ValueError(
            f"{path} has {len(bad):,} row(s) with an unexpected split label, e.g. "
            f"{ex}; expected one of {list(VALID_SPLITS)}.")
    return splits.set_index("ed_key")["split"]


def load_split_labels(persons: pd.DataFrame, splits_path: Path = C.SPLITS_PARQUET) -> pd.Series:
    """Map each person row to its ED's split label.

    Raises unless every person gets one of VALID_SPLITS. An unassigned ed_key
    used to pass silently and then diverge between consumers: graph_build.py
    casts the label to int8, and NaN -> 0 means the GTN TRAINS on those voters,
    while baseline_catboost.py compares against the three names, so the same
    rows drop out of train, val and test alike — and value_counts() hides them
    because it drops NaN. The two models end up fitted on different populations
    and evaluate.py reports them head-to-head.

    splits.parquet is keyed on ed_key, a real key, so it is meant to outlive an
    ETL rerun: stable assignments are what make two training runs comparable.
    So this checks coverage, not provenance — no fingerprint, unlike the
    ordinal-keyed side files in persons_io.py.
    """
    splits = pd.read_parquet(splits_path)
    labels = persons["ed_key"].map(_split_lookup(splits, splits_path))
    if labels.isna().any():
        missing = persons.loc[labels.isna(), "ed_key"]
        null_keys = int(missing.isna().sum())
        examples = missing.dropna().astype(str).drop_duplicates().head(3).tolist()
        detail = []
        if examples:
            detail.append(f"{missing.dropna().nunique():,} unmatched ed_key(s), "
                          f"e.g. {examples}")
        if null_keys:
            detail.append(f"{null_keys:,} person row(s) with a null ed_key")
        raise ValueError(
            f"{splits_path} covers {int(labels.notna().sum()):,} of "
            f"{len(persons):,} persons: " + "; ".join(detail) + ". Rerun "
            f"`python model/splits.py` — note that reassigns EVERY ED, so "
            f"metrics from models trained on the old splits are no longer "
            f"comparable.")
    return labels


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--persons", type=Path, default=C.PERSONS_PARQUET)
    ap.add_argument("--out", type=Path, default=C.SPLITS_PARQUET)
    args = ap.parse_args()

    persons = pd.read_parquet(args.persons, columns=["ed_key", "county"])
    print(f"Assigning ED splits for {persons['ed_key'].nunique():,} EDs "
          f"({len(persons):,} persons)...")
    out = assign_splits(persons)
    out.to_parquet(args.out, index=False)
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
