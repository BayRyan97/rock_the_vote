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

Both attach_* functions live here so the two derived files are validated the
same way, and so the fingerprint helper below has a single home.

Kept intentionally light on imports (pandas/numpy/pyarrow/config only) so every
stage can use it without pulling in pyshp, shapely or CatBoost.
"""
import hashlib
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from pandas.util import hash_pandas_object

import config as C

FINGERPRINT_KEY = b"persons_fingerprint"


def population_fingerprint(persons: pd.DataFrame) -> str:
    """Content hash of WHICH people a table describes, in row order.

    person_id and person_row are row ORDINALS (features_person.assemble assigns
    np.arange), so they identify a position, not a person: any two populations
    of the same size share every id. The derived side files are joined on that
    ordinal, so comparing the ids can only ever detect a length change — which
    is all the old set-equality check here was doing, under a comment claiming
    a key join. Hashing the stable person_uuid column, in order, is what
    actually separates a current file from one describing a different or
    reordered population.
    """
    if "person_uuid" not in persons.columns:
        raise KeyError(
            "persons table has no person_uuid column — it predates the "
            "manifest-driven ETL. Rerun `python model/etl.py`.")
    ids = hash_pandas_object(persons["person_uuid"], index=False).to_numpy()
    return hashlib.blake2b(ids.tobytes(), digest_size=16).hexdigest()


def write_stamped(df: pd.DataFrame, path: Path, fingerprint: str) -> None:
    """Write a derived side file carrying the fingerprint of its source table."""
    table = pa.Table.from_pandas(df, preserve_index=False)
    meta = {**(table.schema.metadata or {}), FINGERPRINT_KEY: fingerprint.encode()}
    pq.write_table(table.replace_schema_metadata(meta), path)


def _check_stamp(path: Path, persons: pd.DataFrame, stage: str) -> None:
    """Fail unless `path` was derived from exactly this persons table."""
    # read_schema reads the footer only — no data pages, no memory spike.
    stamped = (pq.read_schema(path).metadata or {}).get(FINGERPRINT_KEY)
    if stamped is None:
        raise ValueError(
            f"{path} carries no population fingerprint — it was written before "
            f"this check existed. Rerun `python model/{stage}.py` after the ETL.")
    actual = population_fingerprint(persons)
    if stamped.decode() != actual:
        raise ValueError(
            f"{path} is stale: it was derived from a different persons table "
            f"(fingerprint {stamped.decode()[:12]}… != {actual[:12]}…). Rerun "
            f"`python model/{stage}.py` after the ETL.")


def attach_acs(persons: pd.DataFrame,
               path: Path = C.ACS_FEATURES_PARQUET) -> pd.DataFrame:
    """Join ACS block-group features onto a persons table, row-aligned."""
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

    _check_stamp(path, persons, "features_acs")
    acs = pd.read_parquet(path)
    out = persons.merge(acs, on="person_id", how="left", validate="one_to_one")
    assert len(out) == len(persons), "ACS join changed the row count"
    return out


def attach_history(persons: pd.DataFrame,
                   path: Path = C.HISTORY_FEATURES_PARQUET) -> pd.DataFrame:
    """Join history features onto a persons table (row-aligned, with checks)."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found — run `python model/features_history.py` first "
            f"(pass --persons/--out there and --history here for smoke artifacts)")
    # person_row == arange is true of ANY file that stage wrote, so on its own
    # it only ever checked the length. The fingerprint is the real test.
    _check_stamp(path, persons, "features_history")
    hist = pd.read_parquet(path)
    aligned = (len(hist) == len(persons)
               and (hist["person_row"].to_numpy() == np.arange(len(hist))).all())
    if not aligned:
        raise ValueError(f"{path} misaligned with persons table "
                         f"({len(hist):,} vs {len(persons):,} rows) — "
                         f"rerun features_history.py")
    # Assigning a frame of columns aligns on index exactly as concat does,
    # without reallocating every existing block: 0.38s / 670 MB against
    # 0.87s / 1193 MB at 1.85M x 55+29. reset_index already returns a new
    # frame, so the caller's is untouched.
    out = persons.reset_index(drop=True)
    add = hist.drop(columns=["person_row"])
    out[list(add.columns)] = add
    return out


def load_gtn_scores(persons: pd.DataFrame,
                    path: Path = C.SCORES_PARQUET) -> pd.DataFrame:
    """Row-align evaluate.py's GTN scores to a persons table.

    scores.parquet is keyed on person_id, a row ORDINAL, so it has exactly the
    failure mode attach_acs had: a file from a different ETL run reindexes
    cleanly and hands every voter a different voter's probabilities, and a
    partial overlap yields NaN that survives all the way into the database
    (psycopg2 adapts nan to 'NaN'::float and Postgres accepts it into a real).
    Verify the stamp, then verify coverage.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found — it is written by `python model/evaluate.py`, "
            f"the last pipeline stage. CatBoost scores need no GTN artifacts.")
    _check_stamp(path, persons, "evaluate")
    gtn = (pd.read_parquet(path).set_index("person_id")
           .reindex(persons["person_id"].to_numpy()))
    # Belt and braces: the stamp already proves the populations match, but these
    # numbers are written to a production table, so do not infer it.
    missing = int(gtn["turnout_propensity"].isna().sum())
    if missing:
        raise ValueError(
            f"{path} has no GTN score for {missing:,} of {len(persons):,} "
            f"persons. Rerun `python model/evaluate.py` after the ETL.")
    return gtn


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
