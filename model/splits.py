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


def load_split_labels(persons: pd.DataFrame, splits_path: Path = C.SPLITS_PARQUET) -> pd.Series:
    """Map each person row to its ED's split label."""
    splits = pd.read_parquet(splits_path)
    return persons["ed_key"].map(splits.set_index("ed_key")["split"])


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
