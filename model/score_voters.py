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

It now consumes model/artifacts/ instead, so there is exactly one definition
of every feature. Run the pipeline first (etl -> ... -> baseline_catboost).

Write-back is keyed on people.id, carried through the ETL as person_uuid — an
exact primary-key match, not a name join.

Usage:
    python model/score_voters.py                 # dry run: report, write nothing
    python model/score_voters.py --write         # actually UPDATE the database
    python model/score_voters.py --model gtn     # serve GTN scores instead
"""
import argparse
import os
import time
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

import config as C
from catboost_util import score_persons
from persons_io import load_gtn_scores, load_persons




def connect():
    load_dotenv(C.ROOT / ".env.local")
    load_dotenv(C.ROOT / ".env")
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL not set — add it to .env.local")
    return psycopg2.connect(url)


def load_scores(model: str) -> pd.DataFrame:
    """Return (person_uuid, turnout, dem_lean, rep_lean) for every voter."""
    persons = load_persons()
    print(f"  {len(persons):,} voters from {C.PERSONS_PARQUET.name}")

    if model == "catboost":
        s = score_persons(persons)
    else:
        gtn = load_gtn_scores(persons)
        s = pd.DataFrame({
            "turnout": gtn["turnout_propensity"].to_numpy(np.float32),
            "dem_lean": gtn["p_dem_lean"].to_numpy(np.float32),
            "rep_lean": gtn["p_rep_lean"].to_numpy(np.float32),
        }, index=persons.index)

    out = pd.DataFrame({
        "person_uuid": persons["person_uuid"].to_numpy(),
        "turnout_prob": s["turnout"].to_numpy(),
        "dem_lean_prob": s["dem_lean"].to_numpy(),
        "rep_lean_prob": s["rep_lean"].to_numpy(),
        "held_out": (persons["hist_never_voted"] == 1).to_numpy(),
    })
    bad = out["person_uuid"].isna().sum()
    if bad:
        raise SystemExit(f"{bad:,} rows have no person_uuid — was the ETL run "
                         f"with --source csv? Write-back needs the Supabase key.")
    if out["person_uuid"].duplicated().any():
        raise SystemExit("duplicate person_uuid in the scored set; refusing to write")
    return out


def report(df: pd.DataFrame) -> None:
    print("\n-- score distributions " + "-" * 30)
    for c in ("turnout_prob", "dem_lean_prob", "rep_lean_prob"):
        s = df[c]
        print(f"  {c:16s} mean={s.mean():.3f}  p10={s.quantile(.1):.3f}  "
              f"p50={s.quantile(.5):.3f}  p90={s.quantile(.9):.3f}  "
              f">0.90={100 * (s > 0.9).mean():.1f}%")
    hv = df["held_out"]
    print(f"\n  never-voter cohort (held out of the turnout fit): {hv.sum():,}")
    print(f"    mean turnout_prob {df.loc[hv, 'turnout_prob'].mean():.3f} vs "
          f"{df.loc[~hv, 'turnout_prob'].mean():.3f} for everyone else")
    print("    these are genuine GOTV targets, not model failures — see README")


def write_scores(df: pd.DataFrame, conn) -> int:
    """Bulk-load into a temp table, then one set-based UPDATE join.

    Approach taken from main (a068817, "batch score writes") — for 1.85M rows
    a single joined UPDATE beats per-row statements by a wide margin. The temp
    table there declared `id integer`, which cannot hold people.id: that column
    is uuid, and the INSERT fails with InvalidTextRepresentation before a
    single row lands. Typed uuid here.
    """
    records = list(zip(
        df["person_uuid"].tolist(),
        (float(x) for x in df["turnout_prob"]),
        (float(x) for x in df["dem_lean_prob"]),
        (float(x) for x in df["rep_lean_prob"]),
    ))
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
    ap.add_argument("--write", action="store_true",
                    help="actually UPDATE the database; without it this is a dry run")
    args = ap.parse_args()

    t0 = time.time()
    print(f"Loading {args.model} scores...")
    df = load_scores(args.model)
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
