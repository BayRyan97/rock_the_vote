#!/usr/bin/env python3
"""write_supabase.py — push turfs.parquet / turf_assignment.parquet to Supabase.

New tables, not an update to `people`: `turfs` (one row per turf, non-PII)
and `turf_assignment` (one row per target voter, FK to `people.id`). Schema
and RLS policies live in supabase/migrations/017_turfs.sql, applied the same
way every other table in this project is (Supabase CLI/dashboard) — this
script only ever inserts, matching how score_voters.py relates to
migration 001 rather than creating its own tables inline.

Every run is a full snapshot — `turf_id` is a dense integer assigned during
Hilbert-sort at BUILD time (turfs.py), not a stable identity across reruns,
so this always TRUNCATEs and reloads both tables together rather than trying
to upsert against numbering that can shift.

turf_assignment.parquet is keyed on person_row (an ordinal from turfs.py, not
a Supabase id), so this joins it back to person_uuid via persons.parquet the
same way score_voters.py does before writing.

Also backfills `households.turf_id` (migration 018) so the Canvass Map's
existing AD/City-style filter can add a turf option without a new join at
query time — see backfill_household_turf_id() for why that column has no FK
and is reset-then-repopulated every run rather than upserted.

Usage:
    python model/turfs/write_supabase.py                  # dry run: report, write nothing
    python model/turfs/write_supabase.py --write           # actually write
"""
import argparse
import sys
from pathlib import Path

import pandas as pd
import psycopg2.extras

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config as C  # noqa: E402
from db import connect  # noqa: E402


def assert_tables_exist(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema='public' AND table_name IN ('turfs','turf_assignment')
        """)
        have = {r[0] for r in cur.fetchall()}
        missing = {"turfs", "turf_assignment"} - have
        if missing:
            raise SystemExit(
                f"missing table(s) {sorted(missing)} -- apply "
                f"supabase/migrations/017_turfs.sql first (Supabase CLI or dashboard)")

        cur.execute("""
            SELECT 1 FROM information_schema.columns
            WHERE table_schema='public' AND table_name='households' AND column_name='turf_id'
        """)
        if cur.fetchone() is None:
            raise SystemExit(
                "households.turf_id is missing -- apply "
                "supabase/migrations/018_households_turf_id.sql first")


def load_frames(turfs_path: Path, assignment_path: Path, persons_path: Path):
    turfs = pd.read_parquet(turfs_path)
    assignment = pd.read_parquet(assignment_path)
    # person_row is a positional ordinal into persons.parquet, not a Supabase id
    # -- resolve it the same way score_voters.py resolves person_uuid before a
    # write, rather than assuming row order still matches some other frame.
    persons = pd.read_parquet(persons_path, columns=["person_id", "person_uuid"])
    assignment = assignment.merge(
        persons.rename(columns={"person_id": "person_row"}),
        on="person_row", how="left", validate="many_to_one")
    missing = int(assignment["person_uuid"].isna().sum())
    if missing:
        raise ValueError(f"{missing:,} assignment rows have no matching person_uuid "
                         f"-- turf_assignment.parquet and persons.parquet are out of sync")
    return turfs, assignment


def report(turfs: pd.DataFrame, assignment: pd.DataFrame) -> None:
    print(f"  turfs: {len(turfs):,} rows")
    print(f"  turf_assignment: {len(assignment):,} rows")
    print(f"  total expected additional Dem ballots: {turfs['value_dem_ballots'].sum():.1f}")
    print(f"  arms: {turfs['arm'].value_counts().to_dict()}")
    print("\n  top 5 turfs by value:")
    cols = ["turf_id", "n_doors", "value_dem_ballots", "hours_per_ballot", "arm"]
    print(turfs.sort_values("value_dem_ballots", ascending=False)[cols].head(5)
         .to_string(index=False))


def household_turf_map(assignment: pd.DataFrame, persons_path: Path) -> pd.DataFrame:
    """(household_uuid, turf_id) for every household with a turf assignment.

    assignment.hh_id is persons.parquet's household_row (an integer ordinal
    from turfs.py), not households.id -- resolve it via persons.parquet the
    same way person_row is resolved to person_uuid in load_frames().
    """
    hh_turf = assignment[["hh_id", "turf_id"]].drop_duplicates("hh_id")
    persons_hh = (pd.read_parquet(persons_path, columns=["household_row", "household_uuid"])
                  .drop_duplicates("household_row"))
    mapping = hh_turf.merge(persons_hh, left_on="hh_id", right_on="household_row",
                            how="left", validate="one_to_one")
    missing = int(mapping["household_uuid"].isna().sum())
    if missing:
        raise ValueError(f"{missing:,} households in turf_assignment.parquet have no "
                         f"matching household_uuid -- persons.parquet is out of sync")
    return mapping[["household_uuid", "turf_id"]]


def backfill_household_turf_id(assignment: pd.DataFrame, persons_path: Path, conn) -> int:
    """Reset then repopulate households.turf_id from the current snapshot.

    No FK enforces this (see migration 018) because turfs/turf_assignment are
    TRUNCATEd and reloaded whole every run, and households can't be. Reset
    first so a household whose turf assignment changed -- or disappeared,
    e.g. it dropped out of the movable pool -- doesn't keep pointing at a
    turf_id that belonged to a prior run.
    """
    mapping = household_turf_map(assignment, persons_path)
    with conn.cursor() as cur:
        cur.execute("UPDATE households SET turf_id = NULL WHERE turf_id IS NOT NULL")
        cur.execute("CREATE TEMP TABLE _turf_backfill "
                    "(household_id uuid, turf_id integer) ON COMMIT DROP")
        psycopg2.extras.execute_values(
            cur, "INSERT INTO _turf_backfill VALUES %s",
            list(mapping.itertuples(index=False, name=None)), page_size=10_000)
        cur.execute("""
            UPDATE households h SET turf_id = b.turf_id
            FROM _turf_backfill b WHERE h.id = b.household_id
        """)
        return cur.rowcount


def write(turfs: pd.DataFrame, assignment: pd.DataFrame, model: str, persons_path: Path,
         conn) -> None:
    assert_tables_exist(conn)
    with conn.cursor() as cur:
        # The pooler ships a 2min statement_timeout (score_voters.py's own
        # note on this). A session-level SET doesn't stick against it, but it
        # IS honoured for the transaction it's issued in -- and the households
        # backfill UPDATE below (208k rows against a 758k-row table, first
        # time this index has ever been touched) ran past 2 minutes on a real
        # attempt and was cancelled mid-transaction. Set it once, first thing,
        # for the whole transaction this function runs in.
        cur.execute("SET statement_timeout = '15min'")

        # One statement, not child-then-parent: Postgres refuses to TRUNCATE a
        # table another table's FK references unless both are truncated
        # together in the same statement (or CASCADE, which would also silently
        # truncate anything else that ever comes to reference turfs).
        cur.execute("TRUNCATE turf_assignment, turfs")

        turfs = turfs.copy()
        # pyarrow round-trips a list column as numpy.ndarray per cell, not a
        # native Python list; psycopg2 only knows how to adapt the latter for
        # a text[] column.
        turfs["ed_keys_touched"] = turfs["ed_keys_touched"].apply(
            lambda a: list(a) if a is not None else [])

        turf_rows = list(turfs[["turf_id", "n_doors", "n_targets", "value_dem_ballots",
                                "diameter_m", "doors_per_km", "canvasser_hours",
                                "hours_per_ballot", "county", "ed_keys_touched",
                                "is_facility_share", "arm"]].itertuples(index=False, name=None))
        turf_rows = [(*r, model) for r in turf_rows]
        psycopg2.extras.execute_values(
            cur,
            "INSERT INTO turfs (turf_id, n_doors, n_targets, value_dem_ballots, "
            "diameter_m, doors_per_km, canvasser_hours, hours_per_ballot, county, "
            "ed_keys_touched, is_facility_share, arm, model) VALUES %s",
            turf_rows, page_size=5_000)

        assign_rows = list(assignment[["person_uuid", "hh_id", "addr_id", "turf_id", "m_i"]]
                          .itertuples(index=False, name=None))
        psycopg2.extras.execute_values(
            cur,
            "INSERT INTO turf_assignment (person_id, hh_id, addr_id, turf_id, m_i) VALUES %s",
            assign_rows, page_size=10_000)

    n_backfilled = backfill_household_turf_id(assignment, persons_path, conn)
    print(f"  households.turf_id backfilled: {n_backfilled:,} rows")
    conn.commit()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--turfs", type=Path, default=C.TURFS_PARQUET)
    ap.add_argument("--assignment", type=Path, default=C.TURF_ASSIGNMENT_PARQUET)
    ap.add_argument("--persons", type=Path, default=C.PERSONS_PARQUET)
    ap.add_argument("--model", default="catboost")
    ap.add_argument("--write", action="store_true",
                    help="actually TRUNCATE + INSERT; without it this is a dry run")
    args = ap.parse_args()

    print("Loading turfs.parquet / turf_assignment.parquet...")
    turfs, assignment = load_frames(args.turfs, args.assignment, args.persons)
    report(turfs, assignment)

    n_households = len(household_turf_map(assignment, args.persons))

    if not args.write:
        print(f"\n[dry run] would TRUNCATE + INSERT `turfs` ({len(turfs):,} rows) and "
              f"`turf_assignment` ({len(assignment):,} rows), and backfill "
              f"households.turf_id for {n_households:,} households. "
              f"Re-run with --write to apply.")
        return

    print("\nWriting to Supabase...")
    conn = connect()
    try:
        write(turfs, assignment, args.model, args.persons, conn)
    finally:
        conn.close()
    print("  done.")


if __name__ == "__main__":
    main()
