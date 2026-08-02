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

# Rows per committed UPDATE. Small enough that a cancellation costs one chunk
# rather than the run, large enough that per-statement overhead stays noise
# against 1.85M rows.
BATCH_SIZE = 100_000
# Per-statement ceiling, not a reservation: nothing is charged for setting it
# high. It has to clear the slowest single statement here, which is now an index
# rebuild rather than the whole-table UPDATE.
STATEMENT_TIMEOUT = "60min"




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
    """Stage all rows, drop the score indexes, then UPDATE in committed batches.

    Approach taken from main (a068817, "batch score writes") — for 1.85M rows
    a joined UPDATE beats per-row statements by a wide margin. The temp
    table there declared `id integer`, which cannot hold people.id: that column
    is uuid, and the INSERT fails with InvalidTextRepresentation before a
    single row lands. Typed uuid here.

    One joined UPDATE for the whole table is where that stops working. Measured
    2026-08-01 against this instance: the single statement ran 110 minutes, hit
    the ceiling and rolled back every row. Two things were wrong with it, and
    only one was the timeout —

      * an UPDATE that writes an INDEXED column cannot be HOT, so all 14 indexes
        on people took a new entry per row, GIN trigram index included; and
      * all-or-nothing means two hours of work is lost to one cancellation.

    So the indexes on the three score columns come off for the duration (rebuilt
    from their captured definitions in a finally), and the UPDATE is split into
    committed batches. Restartable: re-running rewrites the same values.
    """
    # execute_values paginates its argslist with islice, so this stays lazy;
    # and .tolist() converts in C rather than per element in the interpreter.
    # Measured at 1.85M rows: 7.9s / 193 MB retained against 16.6s / 282 MB,
    # and that memory was held across the INSERT, index build and joined UPDATE.
    t0 = time.time()
    n_batches = max(1, -(-len(df) // BATCH_SIZE))     # ceiling division
    records = zip(
        df["person_uuid"].tolist(),
        df["turnout_prob"].astype(float).tolist(),
        df["dem_lean_prob"].astype(float).tolist(),
        df["rep_lean_prob"].astype(float).tolist(),
        (i // BATCH_SIZE for i in range(len(df))),
    )
    print(f"  {n_batches} batches of up to {BATCH_SIZE:,}")
    with conn.cursor() as cur:
        # The pooler ships a 2min statement_timeout. Raising it here rather than
        # at connect() is deliberate: the pooler ignores connect(options=) and a
        # session-level SET does not stick, but the setting IS honoured for the
        # transaction it is issued in.
        cur.execute(f"SET statement_timeout = '{STATEMENT_TIMEOUT}'")

        # Staging is a REAL table, not the TEMP one this replaces. Batching means
        # committing between chunks, and against a transaction-mode pooler a
        # session is not guaranteed to survive a commit -- a temp table can
        # silently vanish mid-run. Dropped in the finally below.
        cur.execute("DROP TABLE IF EXISTS _score_staging")
        cur.execute("""
            CREATE TABLE _score_staging (
                id         uuid,
                turnout    real,
                dem_lean   real,
                rep_lean   real,
                batch      integer
            )
        """)
        psycopg2.extras.execute_values(
            cur, "INSERT INTO _score_staging VALUES %s", records, page_size=10_000)
        # batch drives the per-chunk filter; id gives the planner a mergeable,
        # unique key into people_pkey instead of only a hash on an unknown set.
        cur.execute("CREATE INDEX ON _score_staging (batch)")
        cur.execute("ALTER TABLE _score_staging ADD PRIMARY KEY (id)")
        cur.execute("ANALYZE _score_staging")
        conn.commit()
        print(f"  staged {len(df):,} rows in {(time.time() - t0) / 60:.1f} min")

        # Capture the real definitions before dropping anything, so the rebuild
        # restores exactly what was there -- including the DESC NULLS LAST
        # ordering, which a hand-written CREATE INDEX would quietly lose.
        # No parameters on this execute, so psycopg2 does not interpolate and a
        # single % is literal.
        cur.execute("""
            SELECT indexname, indexdef FROM pg_indexes
            WHERE schemaname = 'public' AND tablename = 'people'
              AND (indexdef LIKE '%turnout_prob%'
                OR indexdef LIKE '%dem_lean_prob%'
                OR indexdef LIKE '%rep_lean_prob%')
            ORDER BY indexname
        """)
        score_indexes = cur.fetchall()
        if not score_indexes:
            print("  warning: no score indexes found to drop — writing with "
                  "index maintenance live, expect this to be slow")

    updated = 0
    try:
        with conn.cursor() as cur:
            cur.execute(f"SET statement_timeout = '{STATEMENT_TIMEOUT}'")
            # Why this matters more than the batching: an UPDATE that touches an
            # INDEXED column cannot be HOT, so every one of the 14 indexes on
            # people -- including a GIN trigram index on name -- takes a new
            # entry for all 1.85M rows. That is what ran past 110 minutes and
            # lost the whole transaction on 2026-08-01. With the score indexes
            # gone the updated columns are unindexed, and the work drops to the
            # heap plus whatever cannot stay HOT.
            for name, _ in score_indexes:
                print(f"  dropping {name}")
                cur.execute(f'DROP INDEX IF EXISTS "{name}"')
            conn.commit()

            update_sql = """
                UPDATE people SET
                    turnout_prob  = s.turnout,
                    dem_lean_prob = s.dem_lean,
                    rep_lean_prob = s.rep_lean
                FROM _score_staging s
                WHERE people.id = s.id AND s.batch = %s
            """
            # The plan is the thing that decides whether batching helps or hurts.
            # A per-batch Seq Scan on people means n_batches full passes over
            # 1.5 GB -- strictly worse than the single UPDATE this replaces. Print
            # it once so the log says which happened rather than leaving it to be
            # inferred from the timings.
            cur.execute("EXPLAIN " + update_sql, (0,))
            plan = "\n".join(f"      {r[0]}" for r in cur.fetchall())
            print(f"  plan for one batch:\n{plan}", flush=True)
            if "Seq Scan on people" in plan:
                print("  NOTE: batch plan seq-scans people; per-batch cost will "
                      "be a full pass. Watch batch 1 before letting it run on.",
                      flush=True)

            for i in range(n_batches):
                bt = time.time()
                cur.execute(update_sql, (i,))
                n = cur.rowcount           # read before commit, not after
                updated += n
                conn.commit()              # per chunk: progress survives failure
                print(f"  batch {i + 1}/{n_batches}: {n:,} rows "
                      f"({time.time() - bt:.0f}s, {updated:,} total, "
                      f"{(time.time() - t0) / 60:.1f} min elapsed)", flush=True)
    finally:
        # Rebuild even if a batch failed, so a crash cannot leave the app without
        # its score indexes. Each index is committed on its own and its failure
        # is caught: one that cannot be rebuilt must not stop the other three,
        # and none of this may mask the exception that got us here.
        missing = []
        for name, ddl in score_indexes:
            try:
                conn.rollback()      # a failed batch leaves the tx aborted
                with conn.cursor() as cur:
                    cur.execute(f"SET statement_timeout = '{STATEMENT_TIMEOUT}'")
                    bt = time.time()
                    cur.execute(ddl)
                conn.commit()
                print(f"  rebuilt {name} ({time.time() - bt:.0f}s)", flush=True)
            except Exception as exc:                       # noqa: BLE001
                missing.append((name, ddl))
                print(f"  FAILED to rebuild {name}: {exc}", flush=True)
        if missing:
            # The one thing the operator must not have to reconstruct by hand.
            print("\n  !! these indexes are NOT on the table. Run:", flush=True)
            for _, ddl in missing:
                print(f"     {ddl};", flush=True)
        try:
            conn.rollback()
            with conn.cursor() as cur:
                cur.execute("DROP TABLE IF EXISTS _score_staging")
            conn.commit()
        except Exception as exc:                           # noqa: BLE001
            print(f"  note: _score_staging left behind ({exc}); the next run "
                  f"drops it", flush=True)
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
