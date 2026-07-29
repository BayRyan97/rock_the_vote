"""catboost_util.py — manifest-driven feature selection, leakage guards and the
shared CatBoost fit/eval helpers.

Everything that trains or scores a CatBoost model against the flat person table
draws from here: baseline_catboost.py, backtest_temporal.py, export_scores.py
and score_voters.py. Keeping one copy is not tidiness — these functions encode
the leakage rules, and the last time a second copy existed (score_voters.py's
hardcoded feature lists) it drifted far enough to feed cutoff-spanning columns
into the turnout task.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import yaml
from catboost import CatBoostClassifier
from sklearn.metrics import (average_precision_score, brier_score_loss, f1_score,
                             log_loss, roc_auc_score)

import config as C

# One spelling of "missing" for every dtype. Deliberately not "NA": that is a
# plausible real value in a free-text categorical, and a sentinel that can
# collide with data is not a sentinel.
MISSING_CATEGORY = "__MISSING__"

# Version of the categorical RENDERING contract. CatBoost matches features by
# name, not by category values, so a model fitted under an older contract will
# score without error against levels it has never seen. Bump this whenever
# as_category changes the strings it emits.
PREP_CONTRACT = "cat-v2"


def manifest_spec() -> dict:
    return yaml.safe_load(C.MANIFEST.read_text())["features"]


def manifest_features(task: str, available: set,
                      aliases: dict[str, str] | None = None,
                      quiet: bool = False) -> tuple[list[str], list[str]]:
    """Return (numeric, categorical) feature lists for 'turnout' or 'party'.

    `aliases` maps manifest names to local column names, for callers whose
    table spells a column differently (the Supabase pull, mainly). Returned
    names are always the LOCAL ones, ready to index the caller's frame.
    """
    spec = manifest_spec()
    aliases = aliases or {}
    head_tag = f"{task}_head"
    numeric, categorical, missing = [], [], []
    for name, meta in spec.items():
        if "encoder" not in meta["usage"] and head_tag not in meta["usage"]:
            continue
        col = aliases.get(name, name)
        if col not in available:
            missing.append(name)
            continue
        (categorical if meta["type"] == "categorical" else numeric).append(col)
    if missing and not quiet:
        print(f"  [{task}] manifest features not in table (skipped): {missing}")
    return numeric, categorical


def assert_no_cutoff_spanning(task: str, numeric: list[str], categorical: list[str],
                              aliases: dict[str, str] | None = None) -> None:
    """Nothing summarising history THROUGH the export date may inform turnout.

    Those features (tier_*, the household vote-count aggregates, and the
    canvass scores derived from them) contain the target election's outcome.
    """
    if task != "turnout":
        return
    aliases = aliases or {}
    banned = {aliases.get(n, n) for n, m in manifest_spec().items()
              if m.get("spans_cutoff")}
    leaked = banned & set(numeric + categorical)
    assert not leaked, f"turnout feature set contains cutoff-spanning features: {leaked}"


def check_no_exact_recovery(persons: pd.DataFrame, visible: list[str],
                            withheld: list[str], task: str,
                            sample: int = 200_000) -> None:
    """Fail if a withheld feature is an exact sum/difference of two visible ones.

    Withholding a feature from a head accomplishes nothing when the head can
    rebuild it arithmetically. hist_n_votes = hist_n_generals + hist_n_primaries
    defeated the closed-primary rule this way — the party head recovered the
    withheld primary count exactly, for every row. Direct membership checks do
    not see it; this does.
    """
    cols = [c for c in visible if c in persons.columns and
            pd.api.types.is_numeric_dtype(persons[c])]
    hidden = [c for c in withheld if c in persons.columns and
              pd.api.types.is_numeric_dtype(persons[c])]
    if not cols or not hidden:
        return
    idx = persons.index[:sample]
    V = {c: persons.loc[idx, c].to_numpy(np.float64) for c in cols}
    sigs = {v.tobytes(): c for c, v in V.items()}

    hits = []
    for w in hidden:
        wv = persons.loc[idx, w].to_numpy(np.float64)
        for a, av in V.items():
            # a - w == b  =>  w == a - b ;  a + w == b  =>  w == b - a
            for kind, combo in (("sub", av - wv), ("add", av + wv)):
                b = sigs.get(combo.tobytes())
                if b is None or b == a:
                    continue
                hits.append(f"{w} == {a} - {b}" if kind == "sub"
                            else f"{w} == {b} - {a}")
    if hits:
        raise AssertionError(
            f"[{task}] withheld features are exactly recoverable from visible "
            f"ones: {sorted(set(hits))}")
    print(f"  [{task}] no exact linear recovery of {len(hidden)} withheld "
          f"features from {len(cols)} visible ones ({len(idx):,} rows checked)")


def party_withheld(numeric: list[str], categorical: list[str],
                   available: set) -> list[str]:
    """Manifest features the party head does NOT see but that exist in the table."""
    visible = set(numeric + categorical)
    return [n for n in manifest_spec() if n not in visible and n in available]


def as_category(s: pd.Series, fmt: str = "string", name: str = "") -> pd.Series:
    """Render one categorical column to its canonical string form.

    The category string IS the feature — '282' and '282.0' are different levels
    — so the rendering must depend only on the VALUE, never on the column's
    dtype nor on which other rows share the batch. Two traps this closes:

      * `.astype(str)` stringifies missing FIRST, so a trailing `.fillna()`
        never fires; the token that lands is 'nan', '<NA>' or 'None' depending
        on dtype and on whether the frame has been through parquet.
      * a district or ZIP renders as '282' when the column is int16 and '282.0'
        when a single NULL makes it float64, so a cache refresh that changes
        nullability changes EVERY level.

    Hence `format` in the manifest rather than dtype sniffing: an integer_id is
    parsed from whatever representation it arrives in, and a value that is not
    a whole number is invalid data, not something to round.
    """
    if fmt == "string":
        out = s
    elif fmt in ("integer_id", "zip5"):
        num = pd.to_numeric(s, errors="coerce")
        unparsed = num.isna() & s.notna()
        if unparsed.any():
            ex = sorted(s[unparsed].astype(str).drop_duplicates().head(3).tolist())
            raise ValueError(
                f"[{name}] declared format={fmt} but {int(unparsed.sum()):,} "
                f"value(s) are not numeric, e.g. {ex}")
        arr = num[num.notna()].to_numpy(dtype="float64")
        if not np.isfinite(arr).all():
            raise ValueError(
                f"[{name}] declared format={fmt} but "
                f"{int((~np.isfinite(arr)).sum()):,} value(s) are not finite")
        # Exact integrality, element-wise. np.isclose's default rtol scales with
        # magnitude, so it maps 282.0001 -> 282 and 11797.05 -> 11797.
        frac = arr != np.round(arr)
        if frac.any():
            ex = sorted(np.unique(arr[frac])[:3].tolist())
            raise ValueError(
                f"[{name}] declared format={fmt} requires whole numbers; "
                f"{int(frac.sum()):,} value(s) are fractional, e.g. {ex}")
        out = num.round().astype("Int64")
        if fmt == "zip5":
            oob = out.dropna()
            oob = oob[(oob < 0) | (oob > 99999)]
            if len(oob):
                raise ValueError(
                    f"[{name}] declared format=zip5 but {len(oob):,} value(s) are "
                    f"outside 00000-99999, e.g. {sorted(oob.unique()[:3].tolist())}")
            return (out.astype("string").str.zfill(5)
                    .fillna(MISSING_CATEGORY).astype(str))
    else:
        raise ValueError(f"[{name}] unknown manifest format {fmt!r}")
    return out.astype("string").fillna(MISSING_CATEGORY).astype(str)


def prepare(persons: pd.DataFrame, numeric: list[str],
            categorical: list[str],
            aliases: dict[str, str] | None = None) -> pd.DataFrame:
    X = persons[numeric + categorical].copy()
    spec = manifest_spec()
    local_to_manifest = {v: k for k, v in (aliases or {}).items()}
    for c in categorical:
        meta = spec.get(local_to_manifest.get(c, c), {})
        X[c] = as_category(X[c], meta.get("format", "string"), c)
    return X


def report_constant_features(X: pd.DataFrame, task: str) -> list[str]:
    """Name features with one distinct value — they inform nothing.

    manifest_features can only report columns that are ABSENT; a column that is
    present but constant passes every check and shows up in the feature count as
    if it were real. legislative_district did exactly that: all-NA from the
    cache source, real from the CSV source, same .cbm artifact, identical logs.

    A warning rather than an error, because a legitimate smoke subset
    (--county NASSAU --city "GLEN COVE") makes county and town constant by
    construction, and the README documents that workflow.
    """
    dead = [c for c in X.columns if X[c].nunique(dropna=False) <= 1]
    if dead:
        print(f"  [{task}] WARNING: {len(dead)} feature(s) constant across all "
              f"{len(X):,} rows, informing nothing: {dead}")
    else:
        print(f"  [{task}] no constant features ({len(X.columns)} checked)")
    return dead


def cat_indices(X: pd.DataFrame, categorical: list[str]) -> list[int]:
    return [X.columns.get_loc(c) for c in categorical]


def eval_binary(y, p) -> dict:
    return {
        "auc": float(roc_auc_score(y, p)),
        "pr_auc": float(average_precision_score(y, p)),
        "log_loss": float(log_loss(y, p)),
        "brier": float(brier_score_loss(y, p)),
        "base_rate": float(np.mean(y)),
        "n": int(len(y)),
    }


def eval_multiclass(y, proba) -> dict:
    pred = proba.argmax(axis=1)
    return {
        "accuracy": float((pred == y).mean()),
        "log_loss": float(log_loss(y, proba, labels=[0, 1, 2])),
        "macro_f1": float(f1_score(y, pred, average="macro")),
        "n": int(len(y)),
    }


def train_model(X, y, cat_idx, split, quick: bool, loss: str,
                verbose: int = 200):
    params = dict(
        loss_function=loss,
        iterations=150 if quick else 800,
        learning_rate=0.1,
        depth=6,
        early_stopping_rounds=50,
        random_seed=C.SEED,
        verbose=verbose,
    )
    model = CatBoostClassifier(**params)
    model.fit(X[split == "train"], y[split == "train"],
              cat_features=cat_idx,
              eval_set=(X[split == "val"], y[split == "val"]))
    return model


def load_model(path, require_contract: bool = True) -> CatBoostClassifier:
    m = CatBoostClassifier()
    m.load_model(str(path))
    if require_contract:
        got = m.get_metadata().get("prep_contract")
        if got != PREP_CONTRACT:
            raise ValueError(
                f"{path} was fitted under preprocessing contract {got!r}, but this "
                f"code emits {PREP_CONTRACT!r}. The category strings differ, and "
                f"CatBoost matches features by NAME — this model would score "
                f"without error against levels it has never seen. Retrain: "
                f"python model/baseline_catboost.py")
    return m


def score_persons(persons: pd.DataFrame, quiet: bool = False) -> pd.DataFrame:
    """Score a persons frame with the shipped baseline models.

    Returns turnout / dem_lean / rep_lean / other aligned to `persons`. Both
    export_scores.py and score_voters.py go through here so the numbers the
    app serves are the same numbers the export shows.
    """
    available = set(persons.columns)
    num_t, cat_t = manifest_features("turnout", available, quiet=quiet)
    assert_no_cutoff_spanning("turnout", num_t, cat_t)
    turnout = load_model(C.ARTIFACTS / "baseline_turnout.cbm") \
        .predict_proba(prepare(persons, num_t, cat_t))[:, 1]

    num_p, cat_p = manifest_features("party", available, quiet=quiet)
    assert "party" not in num_p + cat_p, "own registration leaked into party model"
    party = load_model(C.ARTIFACTS / "baseline_party.cbm") \
        .predict_proba(prepare(persons, num_p, cat_p))

    return pd.DataFrame({
        "turnout": turnout.astype(np.float32),
        "dem_lean": party[:, 0].astype(np.float32),
        "rep_lean": party[:, 1].astype(np.float32),
        "other": party[:, 2].astype(np.float32),
    }, index=persons.index)
