"""features_history.py — Stage B2: as-of-cutoff behavioral turnout features.

Reads persons.parquet + elections.parquet (both written by etl.py from the
*_Unrolled voter files) and computes, for every voter, vote-history features
that use ONLY ballots cast strictly before the target general election E
(config.TARGET_GENERAL_YEAR): all years < E, plus the year-E primary, which
precedes the November general. Also writes y_voted_general_{E}; etl.py
derives the same outcome as persons.y_turnout (with eligibility masking) —
this copy exists so backtests can rebuild features/labels for any E.

Output history_features.parquet is positionally aligned with persons.parquet
(person_row == row index); consumers join it with attach_history().

Leakage status: y_turnout is the real year-E outcome, so hist_* features are
legitimate for both tasks (manifest.yaml: encoder, with primary-derived
features turnout_head-only — closed primaries make them structurally absent
for BLK voters, the party task's scoring population).

Coverage caveats: county BOE history begins ~1999 and covers only ballots
cast while registered in this county, so rates understate turnout for movers.
Ages in the export are current as of REF_DATE; features de-age them per year.

Usage:
    python model/features_history.py [--persons P] [--elections P] [--out P]
                                     [--target-year YYYY]
"""
import argparse
from pathlib import Path

import numpy as np
import pandas as pd

import config as C

SENTINEL_YEARS = 99                 # "never voted" for years_since_* features
REF_YEAR = int(C.REF_DATE[:4])      # year the export's ages are current as of


def attach_history(persons: pd.DataFrame,
                   path: Path = C.HISTORY_FEATURES_PARQUET) -> pd.DataFrame:
    """Join history features onto a persons table (positional, with checks)."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found — run `python model/features_history.py` first "
            f"(pass --persons/--out there and --history here for smoke artifacts)")
    hist = pd.read_parquet(path)
    aligned = (len(hist) == len(persons)
               and (hist["person_row"].to_numpy() == np.arange(len(hist))).all())
    if not aligned:
        raise ValueError(f"{path} misaligned with persons table "
                         f"({len(hist):,} vs {len(persons):,} rows) — "
                         f"rerun features_history.py")
    return pd.concat([persons.reset_index(drop=True),
                      hist.drop(columns=["person_row"])], axis=1)


def participation_grid(elections: pd.DataFrame, etype: str, n_persons: int,
                       y0: int, y1: int) -> np.ndarray:
    """Bool matrix [n_persons, y1-y0+1]: cast a ballot of etype in that year."""
    sub = elections[elections["etype"] == etype]
    rows = sub["person_row"].to_numpy(np.int64)
    yrs = sub["year"].to_numpy(np.int64)
    n_years = y1 - y0 + 1
    counts = np.bincount(rows * n_years + (yrs - y0), minlength=n_persons * n_years)
    return counts.reshape(n_persons, n_years) > 0


def build_features(persons: pd.DataFrame, elections: pd.DataFrame,
                   target_year: int) -> pd.DataFrame:
    n, E = len(persons), target_year
    y0, y1 = int(elections["year"].min()), int(elections["year"].max())
    if not (y0 < E <= y1):
        raise SystemExit(f"target year {E} outside data years {y0}-{y1}")
    G = participation_grid(elections, "G", n, y0, y1)
    P = participation_grid(elections, "P", n, y0, y1)

    def gcol(y):
        return G[:, y - y0] if y0 <= y <= y1 else np.zeros(n, bool)

    def gwin(a, b):
        """Generals voted in years [a, b], capped to the data range."""
        a_i, b_i = max(a - y0, 0), min(b - y0, y1 - y0)
        if b_i < a_i:
            return np.zeros(n, np.float32)
        return G[:, a_i:b_i + 1].sum(axis=1).astype(np.float32)

    age = persons["age"].to_numpy(np.float64)
    y18 = REF_YEAR - age + 18           # first calendar year the voter is 18+

    def elig_win(a, b):
        """General-election years in [a, b] the voter was 18+ for."""
        return np.clip(b - np.maximum(a, y18) + 1, 0, b - a + 1).astype(np.float32)

    def rate(votes, elig):
        return np.where(elig > 0, votes / np.maximum(elig, 1.0), 0.0).astype(np.float32)

    f = {}
    # --- frequency -------------------------------------------------------
    f["hist_n_generals"] = G[:, :E - y0].sum(axis=1).astype(np.int16)
    f["hist_n_primaries"] = P[:, :E - y0 + 1].sum(axis=1).astype(np.int16)  # incl. year-E primary
    f["hist_n_votes"] = (f["hist_n_generals"] + f["hist_n_primaries"]).astype(np.int16)
    for w in (5, 8, 12):
        f[f"hist_general_rate_{w}"] = rate(gwin(E - w, E - 1), elig_win(E - w, E - 1))
    f["hist_eligible_generals_8"] = elig_win(E - 8, E - 1)

    # --- cycle type (NY holds a general every year; odd years are local) --
    def type_rate(years):
        years = [y for y in years if y >= y0]
        votes, elig = np.zeros(n, np.float32), np.zeros(n, np.float32)
        for y in years:
            votes += gcol(y)
            elig += (y18 <= y)
        return rate(votes, elig)

    f["hist_pres_rate"] = type_rate([y for y in range(E - 16, E) if y % 4 == 0])
    f["hist_midterm_rate"] = type_rate([y for y in range(E - 16, E) if y % 4 == 2])
    f["hist_oddyear_rate"] = type_rate([y for y in range(E - 8, E) if y % 2 == 1])

    # --- recency / tenure -------------------------------------------------
    Gpre = G[:, :E - y0]                     # generals  y0 .. E-1
    A = P[:, :E - y0 + 1].copy()             # any ballot y0 .. E (E col = primary only)
    A[:, :E - y0] |= Gpre

    def years_since_last(M):
        has = M.any(axis=1)
        last_idx = M.shape[1] - 1 - M[:, ::-1].argmax(axis=1)
        return np.where(has, E - (y0 + last_idx), SENTINEL_YEARS).astype(np.int16), has

    f["hist_years_since_last_vote"], has_any = years_since_last(A)
    f["hist_years_since_last_general"], _ = years_since_last(Gpre)
    f["hist_years_since_first_seen"] = np.where(
        has_any, E - (y0 + A.argmax(axis=1)), SENTINEL_YEARS).astype(np.int16)
    f["hist_never_voted"] = (~has_any).astype(np.int8)

    # --- last cycles / streaks / trend ------------------------------------
    for k in (1, 2, 3, 4):
        f[f"hist_voted_g{k}"] = gcol(E - k).astype(np.int8)
    f["hist_voted_primary_cycle"] = P[:, E - y0].astype(np.int8)

    alive, cur = np.ones(n, bool), np.zeros(n, np.int16)
    for y in range(E - 1, y0 - 1, -1):       # consecutive generals ending at E-1
        alive &= gcol(y)
        cur += alive
    run, best = np.zeros(n, np.int16), np.zeros(n, np.int16)
    for y in range(y0, E):                   # longest general-election run
        run = (run + 1) * gcol(y)
        best = np.maximum(best, run)
    f["hist_streak_current"] = cur
    f["hist_streak_longest"] = best
    f["hist_trend_3v5"] = (rate(gwin(E - 3, E - 1), elig_win(E - 3, E - 1))
                           - rate(gwin(E - 8, E - 4), elig_win(E - 8, E - 4)))

    # --- vote-method mix over all pre-E ballots ---------------------------
    prow = elections["person_row"].to_numpy()
    yrs = elections["year"].to_numpy()
    et = elections["etype"].cat.codes.to_numpy()
    me = elections["method"].cat.codes.to_numpy()
    mcats = list(elections["method"].cat.categories)
    p_code = list(elections["etype"].cat.categories).index("P")
    pre = (yrs < E) | ((yrs == E) & (et == p_code))
    total = np.bincount(prow[pre], minlength=n).astype(np.float32)

    def method_share(chars):
        sel = pre & np.isin(me, [mcats.index(c) for c in chars])
        return rate(np.bincount(prow[sel], minlength=n).astype(np.float32), total)

    f["hist_share_early"] = method_share(["V"])
    f["hist_share_absentee"] = method_share(["A", "M", "F"])
    f["hist_share_pollsite"] = method_share(["E", "D"])

    # --- age at the target election (for stacked-E training later) --------
    f["hist_age_at_target"] = (age - (REF_YEAR - E)).astype(np.float32)

    # --- household / ED context, leave-self-out ---------------------------
    r8 = pd.Series(f["hist_general_rate_8"], index=persons.index, dtype=np.float64)
    for col, key in (("hist_hh_general_rate_8_excl", "household_row"),
                     ("hist_ed_general_rate_8_excl", "ed_key")):
        g = r8.groupby(persons[key])
        cnt = g.transform("size")
        f[col] = (((g.transform("sum") - r8) / (cnt - 1))
                  .where(cnt > 1, 0.0).astype(np.float32).to_numpy())

    # --- label: did they vote in the year-E general? ----------------------
    out = pd.DataFrame(f)
    out.insert(0, "person_row", np.arange(n, dtype=np.int32))
    out[f"y_voted_general_{E}"] = G[:, E - y0].astype(np.int8)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--persons", type=Path, default=C.PERSONS_PARQUET)
    ap.add_argument("--elections", type=Path, default=C.ELECTIONS_PARQUET)
    ap.add_argument("--out", type=Path, default=C.HISTORY_FEATURES_PARQUET)
    ap.add_argument("--target-year", type=int, default=C.TARGET_GENERAL_YEAR)
    args = ap.parse_args()

    persons = pd.read_parquet(
        args.persons, columns=["age", "household_row", "ed_key", "tier_count"])
    elections = pd.read_parquet(args.elections)
    print(f"{len(persons):,} persons, {len(elections):,} ballots; "
          f"target general = {args.target_year}")

    out = build_features(persons, elections, args.target_year)
    label = f"y_voted_general_{args.target_year}"
    y = out[label].to_numpy()
    print(f"  {label} mean: {y.mean():.3f}")
    print(f"  hist_never_voted (pre-{args.target_year}): "
          f"{out['hist_never_voted'].mean():.3f}")
    corr = np.corrcoef(persons["tier_count"].to_numpy(np.float64),
                       out["hist_n_votes"].to_numpy(np.float64))[0, 1]
    print(f"  corr(tier_count, hist_n_votes) = {corr:.3f}  (parse sanity)")
    try:
        from sklearn.metrics import roc_auc_score
        for c in ("hist_general_rate_8", "hist_voted_g4", "hist_n_votes"):
            print(f"  AUC[{c} -> {label}] = {roc_auc_score(y, out[c]):.4f}")
        print(f"  AUC[age -> {label}] = "
              f"{roc_auc_score(y, persons['age']):.4f}  (demographic floor)")
    except ImportError:
        pass

    out.to_parquet(args.out, index=False)
    print(f"Wrote {args.out} ({out.shape[1] - 2} features + person_row + {label})")


if __name__ == "__main__":
    main()
