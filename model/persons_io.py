"""persons_io.py — the one way to load the flat person table.

Feature stages DERIVE side files; they never mutate persons.parquet. Consumers
call load_persons(), which joins them back on. This is deliberate:

    features_acs.py used to add its columns to persons.parquet in place. Any
    etl.py rerun then silently dropped all 9 of them, and nothing noticed —
    retraining just produced a valid-looking model on 60 features instead of
    69, and scoring an existing model failed with "Invalid cat_features[0]",
    which points nowhere near the actual cause.

Deriving instead of mutating makes the stage idempotent and order-independent:
re-running the ETL cannot destroy ACS, because ACS was never written there.
Staleness is caught loudly here rather than showing up as a quiet accuracy
drop downstream.

Kept intentionally light on imports (pandas/numpy/config only) so every stage
can use it without pulling in pyshp, shapely or CatBoost.
"""
from pathlib import Path

import numpy as np
import pandas as pd

import config as C
from features_history import attach_history


def attach_acs(persons: pd.DataFrame,
               path: Path = C.ACS_FEATURES_PARQUET) -> pd.DataFrame:
    """Join ACS block-group features onto a persons table, keyed on person_id."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found — run `python model/features_acs.py` first")
    # A persons.parquet written by the OLD in-place features_acs still carries
    # these columns; joining would produce _x/_y suffixes and quietly break
    # manifest lookups. Fail with the actual remedy instead.
    stale = [c for c in persons.columns if c.startswith("acs_") or c == "bg_geoid"]
    if stale:
        raise ValueError(
            f"persons table already contains {len(stale)} ACS column(s) "
            f"({stale[:3]}...) — it was written by the old in-place "
            f"features_acs.py. Rerun `python model/etl.py` to regenerate a "
            f"clean persons.parquet, then `python model/features_acs.py`.")

    acs = pd.read_parquet(path)

    # Key join, not positional: this file outlives ETL runs, so the real risk
    # is that it describes a DIFFERENT population, not that it is misordered.
    have, want = set(acs["person_id"]), set(persons["person_id"])
    if have != want:
        raise ValueError(
            f"{path} is stale: {len(want - have):,} persons have no ACS row and "
            f"{len(have - want):,} ACS rows match no person "
            f"({len(acs):,} vs {len(persons):,} rows). Rerun "
            f"`python model/features_acs.py` after the ETL.")

    out = persons.merge(acs, on="person_id", how="left", validate="one_to_one")
    assert len(out) == len(persons), "ACS join changed the row count"
    return out


def load_persons(persons_path: Path = C.PERSONS_PARQUET,
                 acs_path: Path = C.ACS_FEATURES_PARQUET,
                 history_path: Path = C.HISTORY_FEATURES_PARQUET,
                 with_acs: bool = True,
                 with_history: bool = True) -> pd.DataFrame:
    """Read persons.parquet and join the derived feature files onto it.

    Every model stage should go through here — a stage that hand-rolls
    read_parquet() silently trains on whatever subset of features happens to
    be in the base table.
    """
    persons = pd.read_parquet(persons_path)
    if with_acs:
        persons = attach_acs(persons, acs_path)
    if with_history:
        persons = attach_history(persons, history_path)
    return persons
