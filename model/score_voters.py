#!/usr/bin/env python3
"""score_voters.py — write model scores back to Supabase.

Reads the scored population produced by the pipeline and updates
people.turnout_prob / dem_lean_prob / rep_lean_prob.

This script used to train its own models directly off Supabase, with its own
feature engineering. That second implementation drifted from the pipeline in
three ways that all produced wrong numbers under the same feature names:

  * hist_general_rate_* divided by the window width instead of the years the
    voter was actually 18+, inflating rates ~2.7x for young voters;
  * hist_eligible_8 was a constant, not per-voter eligibility;
  * ed_key omitted assembly_district, merging distinct EDs that share a
    number across ADs — into one split unit AND one ED-aggregate group.

It now consumes config.ARTIFACTS instead, so there is exactly one definition
of every feature. Run the pipeline first (etl -> ... -> baseline_catboost).

Write-back is keyed on people.id, carried through the ETL as person_uuid — an
exact primary-key match, not a name join.

Usage:
    python model/score_voters.py                 # dry run: report, write nothing
    python model/score_voters.py --write         # actually UPDATE the database
    python model/score_voters.py --model gtn     # serve GTN scores instead
"""
import argparse
import time
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2.extras

import config as C
from calibration import to_base_rate
from catboost_util import print_score_distribution, score_persons
from db import connect
from persons_io import load_gtn_scores, load_persons, read_stamp

# people.id is a Postgres uuid. sources.from_csv fills person_uuid with a
# 16-char blake2b digest instead, which is non-null -- so an isna() check here
# passed and the run died inside the INSERT instead.
UUID_RE = (r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
           r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")




def load_scores(model: str, history_path, base_rate) -> pd.DataFrame:
    """Return (person_uuid, turnout, dem_lean, rep_lean) for every voter."""
    persons = load_persons(history_path=history_path)
    vintage = read_stamp(history_path, "history_target_year") or "unknown"
    print(f"  {len(persons):,} voters from {C.PERSONS_PARQUET.name}; "
          f"turnout history as-of {vintage}")

    if model == "catboost":
        # The party head is fitted and validated against the TRAINING vintage
        # and is measurably worse anywhere else — see score_persons. Load it
        # separately rather than holding two 94-column frames at once.
        if Path(history_path) != C.HISTORY_FEATURES_PARQUET:
            train_v = read_stamp(C.HISTORY_FEATURES_PARQUET, "history_target_year")
            print(f"  party head scored on the training vintage ({train_v})")
            party_persons = load_persons(history_path=C.HISTORY_FEATURES_PARQUET)
        else:
            party_persons = persons
        s = score_persons(persons, party_persons)
        del party_persons
    else:
        gtn = load_gtn_scores(persons)
        s = pd.DataFrame({
            "turnout": gtn["turnout_propensity"].to_numpy(np.float32),
            "dem_lean": gtn["p_dem_lean"].to_numpy(np.float32),
            "rep_lean": gtn["p_rep_lean"].to_numpy(np.float32),
        }, index=persons.index)

    # Both heads are fitted on TARGET_GENERAL_YEAR, a presidential year, and
    # served for SERVE_GENERAL_YEAR, a midterm. Only the turnout level is
    # cycle-specific — party registration is not — so the shift applies here
    # alone, and being monotone it leaves every ranking intact.
    turnout = to_base_rate(s["turnout"].to_numpy(), base_rate)

    out = pd.DataFrame({
        "person_uuid": persons["person_uuid"].to_numpy(),
        "turnout_prob": turnout,
        "dem_lean_prob": s["dem_lean"].to_numpy(),
        "rep_lean_prob": s["rep_lean"].to_numpy(),
        "held_out": (persons["hist_never_voted"] == 1).to_numpy(),
    })
    uu = out["person_uuid"].astype("string")
    bad = ~uu.str.match(UUID_RE, na=False)
    if bad.any():
        ex = uu[bad].dropna().drop_duplicates().head(3).tolist()
        raise SystemExit(
            f"{int(bad.sum()):,} of {len(out):,} rows have no Supabase "
            f"person_uuid (e.g. {ex}) — sources.from_csv emits a synthetic "
            f"blake2b digest, not a uuid, and write-back is keyed on people.id. "
            f"Re-run the ETL with the default --source cache.")
    if out["person_uuid"].duplicated().any():
        raise SystemExit("duplicate person_uuid in the scored set; refusing to write")
    return out


def report(df: pd.DataFrame) -> None:
    print_score_distribution(df, ("turnout_prob", "dem_lean_prob", "rep_lean_prob"))
    hv = df["held_out"]
    print(f"\n  no prior ballot at this history cutoff: {hv.sum():,}")
    if hv.any():
        print(f"    mean turnout_prob {df.loc[hv, 'turnout_prob'].mean():.3f} vs "
              f"{df.loc[~hv, 'turnout_prob'].mean():.3f} for everyone else")
    else:
        print("    none — every voter has a ballot before the serving cutoff")


def write_scores(df: pd.DataFrame, conn) -> int:
    """Bulk-load into a temp table, then one set-based UPDATE join.

    Approach taken from main (a068817, "batch score writes") — for 1.85M rows
    a single joined UPDATE beats per-row statements by a wide margin. The temp
    table there declared `id integer`, which cannot hold people.id: that column
    is uuid, and the INSERT fails with InvalidTextRepresentation before a
    single row lands. Typed uuid here.
    """
    # execute_values paginates its argslist with islice, so this stays lazy;
    # and .tolist() converts in C rather than per element in the interpreter.
    # Measured at 1.85M rows: 7.9s / 193 MB retained against 16.6s / 282 MB,
    # and that memory was held across the INSERT, index build and joined UPDATE.
    records = zip(
        df["person_uuid"].tolist(),
        df["turnout_prob"].astype(float).tolist(),
        df["dem_lean_prob"].astype(float).tolist(),
        df["rep_lean_prob"].astype(float).tolist(),
    )
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TEMP TABLE _score_updates (
                id         uuid,
                turnout    real,
                dem_lean   real,
                rep_lean   real
            ) ON COMMIT DROP
        """)
        psycopg2.extras.execute_values(
            cur, "INSERT INTO _score_updates VALUES %s", records, page_size=10_000)
        cur.execute("CREATE INDEX ON _score_updates (id)")
        cur.execute("ANALYZE _score_updates")
        cur.execute("""
            UPDATE people SET
                turnout_prob  = s.turnout,
                dem_lean_prob = s.dem_lean,
                rep_lean_prob = s.rep_lean
            FROM _score_updates s
            WHERE people.id = s.id
        """)
        updated = cur.rowcount
    conn.commit()
    return updated


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", choices=("catboost", "gtn"), default="catboost",
                    help="which scores to serve (default: catboost — it wins "
                         "turnout 0.8886 vs 0.8855 and ties on party)")
    ap.add_argument("--history", type=Path, default=C.HISTORY_SERVE_PARQUET,
                    help=f"history vintage to SCORE from (default: as-of "
                         f"{C.SERVE_GENERAL_YEAR}). Pass the training vintage to "
                         f"reproduce pre-split numbers.")
    ap.add_argument("--base-rate", type=float, default=None,
                    help=f"expected turnout for the served election (default: "
                         f"config.SERVE_BASE_RATE={C.SERVE_BASE_RATE}, applied "
                         f"only when serving the as-of-{C.SERVE_GENERAL_YEAR} "
                         f"vintage). Pass 0 to serve the fitted level unshifted.")
    ap.add_argument("--write", action="store_true",
                    help="actually UPDATE the database; without it this is a dry run")
    args = ap.parse_args()

    # The shift describes the election being SERVED. Asking for the training
    # vintage is a request to reproduce training-vintage numbers, so that stays
    # unshifted unless --base-rate overrides it explicitly.
    if args.base_rate is None:
        base_rate = (C.SERVE_BASE_RATE
                     if Path(args.history) == C.HISTORY_SERVE_PARQUET else None)
    else:
        base_rate = args.base_rate or None

    t0 = time.time()
    print(f"Loading {args.model} scores...")
    df = load_scores(args.model, args.history, base_rate)
    report(df)

    if not args.write:
        print(f"\n[dry run] would update {len(df):,} rows. "
              f"Re-run with --write to apply.")
        return

    print(f"\nWriting {len(df):,} scores to the database...")
    conn = connect()
    try:
        n = write_scores(df, conn)
    finally:
        conn.close()
    print(f"  updated {n:,} rows in {(time.time() - t0) / 60:.1f} min")


if __name__ == "__main__":
    main()
