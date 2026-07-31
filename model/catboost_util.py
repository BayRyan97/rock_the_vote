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
from catboost import CatBoostClassifier
from sklearn.metrics import (average_precision_score, brier_score_loss, f1_score,
                             log_loss, roc_auc_score)

import config as C
# The manifest reader lives in config so the GTN path can use it without
# importing CatBoost. Re-exported here for the existing call sites.
from config import manifest_spec, validate_spec  # noqa: F401

# One spelling of "missing" for every dtype. Deliberately not "NA": that is a
# plausible real value in a free-text categorical, and a sentinel that can
# collide with data is not a sentinel.
MISSING_CATEGORY = "__MISSING__"

# Version of the categorical RENDERING contract. CatBoost matches features by
# name, not by category values, so a model fitted under an older contract will
# score without error against levels it has never seen. Bump this whenever
# as_category changes the strings it emits.
PREP_CONTRACT = "cat-v2"


def manifest_features(task: str, available: set,
                      quiet: bool = False) -> tuple[list[str], list[str]]:
    """Return (numeric, categorical) feature lists for 'turnout' or 'party'.

    Manifest names ARE column names. An `aliases` parameter used to exist for a
    caller whose table spelled columns differently (score_voters.py reading
    Supabase directly); that caller now reads the pipeline's artifacts, and the
    unused parameter had already caused party_withheld to compare manifest
    names against local ones. Reintroduce it with a caller and a test if a
    differently-spelled source ever returns.
    """
    head_tag = f"{task}_head"
    numeric, categorical, missing = [], [], []
    for name, meta in manifest_spec().items():
        if "encoder" not in meta["usage"] and head_tag not in meta["usage"]:
            continue
        if name not in available:
            missing.append(name)
            continue
        (categorical if meta["type"] == "categorical" else numeric).append(name)
    if missing and not quiet:
        print(f"  [{task}] manifest features not in table (skipped): {missing}")
    return numeric, categorical


def assert_no_cutoff_spanning(task: str, numeric: list[str],
                              categorical: list[str]) -> None:
    """Nothing summarising history THROUGH the export date may inform turnout.

    Those features (tier_*, the household vote-count aggregates, and the
    canvass scores derived from them) contain the target election's outcome.
    """
    if task != "turnout":
        return
    banned = {n for n, m in manifest_spec().items() if m.get("spans_cutoff")}
    leaked = banned & set(numeric + categorical)
    assert not leaked, f"turnout feature set contains cutoff-spanning features: {leaked}"


RECOVERY_RTOL, RECOVERY_ATOL = 1e-6, 1e-9
RECOVERY_MIN_ROWS = 1_000


def check_no_exact_recovery(persons: pd.DataFrame, visible: list[str],
                            withheld: list[str], task: str,
                            sample: int = 200_000, seed: int = 0) -> None:
    """Fail if a withheld feature is a sum/difference of two visible ones.

    Withholding a feature accomplishes nothing when the head can rebuild it
    arithmetically: hist_n_votes = hist_n_generals + hist_n_primaries defeated
    the closed-primary rule that way, and no membership check sees it.

    Compared to storage precision, not bit-exactly. The previous version cast to
    float64 and compared .tobytes(), so an identity that holds exactly in the
    float32 the features are STORED as matched on only ~69% of rows and was
    missed — while still printing the same reassuring line. Tolerance is 1e-6
    relative, ~8x float32 epsilon; two unrelated columns agreeing that closely
    across 200k rows does not happen.

    Constant columns are dropped first. A constant withheld column has nothing
    to leak and a constant visible column cannot help reconstruct anything, and
    keeping them made byte-identical all-zero columns (a smoke subset with no
    matched donors) raise a bogus AssertionError that killed the stage.

    Finds PAIRS only: w == a + b - c and non-linear reconstructions are out of
    scope, because the combinatorics grow and the known failure mode was a pair.
    """
    def load(names):
        out = {}
        for c in names:
            if c not in persons.columns or not pd.api.types.is_numeric_dtype(persons[c]):
                continue
            v = persons[c].to_numpy(np.float64)[:sample]
            if v.max() == v.min():        # constant, or all-NaN
                continue
            out[c] = v
        return out

    V, W = load(visible), load(withheld)
    if not V or not W:
        return
    n = len(next(iter(V.values())))
    probe = np.random.default_rng(seed).standard_normal(n)

    hits, covered = [], 0
    for w, wv in W.items():
        m = np.isfinite(wv)
        if m.sum() < RECOVERY_MIN_ROWS:
            continue
        # A visible column that is NaN where w is defined cannot take part in an
        # identity that holds for every row of w.
        cand = {c: v[m] for c, v in V.items() if np.isfinite(v[m]).all()}
        if len(cand) < 2:
            continue
        covered += 1
        pm, wm = probe[m], wv[m]
        sw = float(wm @ pm)
        s = {c: float(v @ pm) for c, v in cand.items()}
        # Scalar prescreen, then verify the survivors on the full vectors.
        # Iterating ORDERED pairs covers w == a - b and w == b - a in one pass;
        # the old code ran a second "add" pass for the same coverage.
        for a, av in cand.items():
            target = s[a] - sw
            for b, bv in cand.items():
                if b == a or abs(s[b] - target) > 1e-6 * max(1.0, abs(target)):
                    continue
                if np.allclose(av - wm, bv, rtol=RECOVERY_RTOL, atol=RECOVERY_ATOL):
                    hits.append(f"{w} == {a} - {b}")
    if hits:
        raise AssertionError(
            f"[{task}] withheld features are recoverable from visible ones: "
            f"{sorted(set(hits))}")
    print(f"  [{task}] no linear recovery of {covered} of {len(W)} withheld "
          f"features from {len(V)} visible ones ({n:,} rows, "
          f"rtol={RECOVERY_RTOL:g})")


def party_withheld(numeric: list[str], categorical: list[str],
                   available: set) -> list[str]:
    """Manifest features the party head does NOT see but that exist in the table.

    Correct only because manifest names and column names are the same namespace.
    They were not while manifest_features took an `aliases` map: this compared
    manifest keys against local names and was wrong in both directions. The map
    is gone; if it returns, this needs to speak the same namespace as its inputs.
    """
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
            categorical: list[str]) -> pd.DataFrame:
    # The .copy() looks redundant -- persons[cols] is already independent -- but
    # removing it is not a win. Measured at 1.85M rows, best of 3 over the whole
    # function: 53.6s / 680 MB with, 51.8s / 680 MB without, i.e. noise, because
    # the as_category loop below dominates. Without it the assignments are on a
    # slice and pandas raises SettingWithCopyWarning. Keep it.
    X = persons[numeric + categorical].copy()
    spec = manifest_spec()
    for c in categorical:
        X[c] = as_category(X[c], spec.get(c, {}).get("format", "string"), c)
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


def print_score_distribution(df: pd.DataFrame, cols, width: int = 16) -> None:
    """Shared by score_voters.py and export_scores.py.

    Those two artifacts get compared side by side, so they have to be computed
    the same way: a change to the quantiles or the >0.90 threshold in one copy
    silently made them non-comparable.
    """
    print("\n-- score distributions " + "-" * 30)
    for c in cols:
        s = df[c]
        print(f"  {c:{width}s} mean={s.mean():.3f}  p10={s.quantile(.1):.3f}  "
              f"p50={s.quantile(.5):.3f}  p90={s.quantile(.9):.3f}  "
              f">0.90={100 * (s > 0.9).mean():.1f}%")


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


def score_turnout(persons: pd.DataFrame, quiet: bool = False) -> np.ndarray:
    """P(votes in the target general). Wants history as-of the election PREDICTED."""
    num, cat = manifest_features("turnout", set(persons.columns), quiet=quiet)
    assert_no_cutoff_spanning("turnout", num, cat)
    return (load_model(C.ARTIFACTS / "baseline_turnout.cbm")
            .predict_proba(prepare(persons, num, cat))[:, 1].astype(np.float32))


def score_party(persons: pd.DataFrame, quiet: bool = False) -> np.ndarray:
    """P(dem/rep/other lean). Wants the TRAINING vintage — see score_persons."""
    num, cat = manifest_features("party", set(persons.columns), quiet=quiet)
    assert "party" not in num + cat, "own registration leaked into party model"
    return (load_model(C.ARTIFACTS / "baseline_party.cbm")
            .predict_proba(prepare(persons, num, cat)).astype(np.float32))


def score_persons(persons: pd.DataFrame, party_persons: pd.DataFrame | None = None,
                  quiet: bool = False) -> pd.DataFrame:
    """Score a persons frame with the shipped baseline models.

    Returns turnout / dem_lean / rep_lean / other aligned to `persons`. Both
    export_scores.py and score_voters.py go through here so the numbers the
    app serves are the same numbers the export shows.

    The two heads want DIFFERENT feature vintages, and it is not symmetric:

      * y_turnout is the target election's outcome, so training pairs features
        and label at the same cutoff. Serving with history as-of the election
        being predicted asks the same question about that election.
      * y_party is the registration snapshot already in the file. Training
        therefore ALREADY pairs training-vintage features with a present-day
        label, and serving at a later vintage is a pairing that was never fitted
        or validated. Measured: implied dem share 0.478 against a true 0.521,
        eight times the training vintage's error, flipping the file's aggregate
        lean. Registration does not move with the history cutoff, so a later
        vintage buys the party head nothing.

    Pass `party_persons` (the training vintage) when serving. It defaults to
    `persons` for single-vintage callers such as the smoke workflow.
    """
    turnout = score_turnout(persons, quiet=quiet)
    party = score_party(persons if party_persons is None else party_persons,
                        quiet=quiet)
    return pd.DataFrame({
        "turnout": turnout,
        "dem_lean": party[:, 0],
        "rep_lean": party[:, 1],
        "other": party[:, 2],
    }, index=persons.index)
