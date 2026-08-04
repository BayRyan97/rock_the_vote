#!/usr/bin/env python3
"""write_supabase.py — push the turf build to Supabase.

Three tables, not an update to `people`: `turfs` (one row per walkable turf,
non-PII), `facilities` (one row per apartment building, non-PII) and
`turf_assignment` (one row per target voter, FK to `people.id`). Schema and
RLS policies live in supabase/migrations/017_turfs.sql, 019_turfs_value_
columns.sql and 020_facilities.sql, applied the same way every other table in
this project is (Supabase CLI/dashboard) — this script only ever inserts,
matching how score_voters.py relates to migration 001 rather than creating its
own tables inline.

Every run is a full snapshot — `turf_id` is a dense integer assigned during
Hilbert-sort at BUILD time (turfs.py), not a stable identity across reruns,
so this always TRUNCATEs and reloads all three tables together rather than
trying to upsert against numbering that can shift.

turf_assignment.parquet is keyed on person_row and facilities.parquet on
hh_id (ordinals from turfs.py, not Supabase ids), so this joins both back via
persons.parquet the same way score_voters.py does before writing.

Also backfills `households.turf_id` (migration 018) and `households.is_facility`
(020) so the Canvass Map's existing AD/City-style filter can add a turf option
without a new join at query time — see backfill_household_turf_id() for why
turf_id has no FK, why it is reset-then-repopulated every run, and why
facility households get their nearest turf rather than NULL.

Usage:
    python model/turfs/write_supabase.py                  # dry run: report, write nothing
    python model/turfs/write_supabase.py --write           # actually write
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2.extras

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config as C  # noqa: E402
from db import connect  # noqa: E402


def assert_tables_exist(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema='public'
              AND table_name IN ('turfs','turf_assignment','facilities')
        """)
        have = {r[0] for r in cur.fetchall()}
        missing = {"turfs", "turf_assignment"} - have
        if missing:
            raise SystemExit(
                f"missing table(s) {sorted(missing)} -- apply "
                f"supabase/migrations/017_turfs.sql first (Supabase CLI or dashboard)")
        if "facilities" not in have:
            raise SystemExit(
                "facilities is missing -- apply "
                "supabase/migrations/020_facilities.sql first")

        cur.execute("""
            SELECT table_name, column_name FROM information_schema.columns
            WHERE table_schema='public'
              AND (   (table_name='households' AND column_name IN ('turf_id','is_facility'))
                   OR (table_name='turfs'      AND column_name='value_net_margin'))
        """)
        cols = {(r[0], r[1]) for r in cur.fetchall()}
        for table, col, mig in (("households", "turf_id", "018_households_turf_id.sql"),
                                ("turfs", "value_net_margin", "019_turfs_value_columns.sql"),
                                ("households", "is_facility", "020_facilities.sql")):
            if (table, col) not in cols:
                raise SystemExit(f"{table}.{col} is missing -- apply "
                                 f"supabase/migrations/{mig} first")


VALID_ARMS = {"treatment", "control", "buffer"}


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


def load_facilities(facilities_path: Path, persons_path: Path) -> pd.DataFrame:
    """facilities.parquet with hh_id resolved to households.id.

    Same reason load_frames resolves person_row: hh_id is persons.parquet's
    household_row ordinal, not a Supabase uuid, and 020_facilities.sql keys on
    a real FK rather than persisting a per-run ordinal.
    """
    fac = pd.read_parquet(facilities_path)
    if fac.empty:
        return fac.assign(household_uuid=pd.Series(dtype="object"))
    persons_hh = (pd.read_parquet(persons_path, columns=["household_row", "household_uuid"])
                  .drop_duplicates("household_row"))
    fac = fac.merge(persons_hh, left_on="hh_id", right_on="household_row",
                    how="left", validate="one_to_one")
    missing = int(fac["household_uuid"].isna().sum())
    if missing:
        raise ValueError(f"{missing:,} facility rows have no matching household_uuid "
                         f"-- facilities.parquet and persons.parquet are out of sync")
    return fac


def check_arms(turfs: pd.DataFrame) -> None:
    """Reject a bad arm value BEFORE the TRUNCATE, not after.

    017_turfs.sql constrains arm to (treatment, control, buffer). Discovering a
    violation via Postgres means the CHECK fires mid-transaction, after both
    tables have already been dropped -- a failed run that also destroyed the
    previous snapshot. turfs.py raises on this too; this is the second gate,
    because the parquet on disk may predate that fix.
    """
    bad = set(turfs["arm"].dropna().unique()) - VALID_ARMS
    if bad or turfs["arm"].isna().any():
        raise SystemExit(
            f"turfs.parquet contains arm value(s) the DB will reject: "
            f"{sorted(bad) or ['<null>']}. Allowed: {sorted(VALID_ARMS)}. "
            f"Rebuild with model/turfs/turfs.py rather than writing this.")


def report(turfs: pd.DataFrame, assignment: pd.DataFrame, facilities: pd.DataFrame) -> None:
    print(f"  turfs: {len(turfs):,} rows")
    print(f"  turf_assignment: {len(assignment):,} rows")
    print(f"  facilities: {len(facilities):,} rows")
    # Two different units: ballots produced vs two-party margin. Print both, and
    # do not let anyone read one as the other.
    print(f"  walk list: {turfs['value_net_margin'].sum():.1f} net margin "
         f"({turfs['value_dem_ballots'].sum():.1f} gross Dem ballots)")
    if not facilities.empty:
        print(f"  facilities held out of the walk list: "
             f"{facilities['value_net_margin'].sum():.1f} net margin across "
             f"{int(facilities['n_targets'].sum()):,} targets")
    print(f"  arms: {turfs['arm'].value_counts().to_dict()}")
    print("\n  top 5 turfs by net margin:")
    cols = ["turf_id", "n_doors", "targets_per_door", "value_net_margin",
            "value_dem_ballots", "hours_per_net_margin", "arm"]
    print(turfs.sort_values("value_net_margin", ascending=False)[cols].head(5)
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


def backfill_household_turf_id(assignment: pd.DataFrame, facilities: pd.DataFrame,
                               persons_path: Path, conn) -> tuple[int, int]:
    """Reset then repopulate households.turf_id and households.is_facility.

    No FK enforces turf_id (see migration 018) because turfs/turf_assignment
    are TRUNCATEd and reloaded whole every run, and households can't be. Reset
    first so a household whose turf assignment changed -- or disappeared,
    e.g. it dropped out of the movable pool -- doesn't keep pointing at a
    turf_id that belonged to a prior run.

    Facility households are given their NEAREST turf_id rather than NULL. They
    are deliberately absent from turfs.n_doors and the value columns -- that is
    what makes the walk-list ranking honest -- but the Canvass Map filters
    households ON this column, so NULLing them would silently drop every
    apartment building off the map. is_facility is what lets the UI draw them
    as a building rather than a door.
    """
    mapping = household_turf_map(assignment, persons_path)
    with conn.cursor() as cur:
        cur.execute("UPDATE households SET turf_id = NULL WHERE turf_id IS NOT NULL")
        cur.execute("UPDATE households SET is_facility = false WHERE is_facility")
        cur.execute("CREATE TEMP TABLE _turf_backfill "
                    "(household_id uuid, turf_id integer) ON COMMIT DROP")
        psycopg2.extras.execute_values(
            cur, "INSERT INTO _turf_backfill VALUES %s",
            list(mapping.itertuples(index=False, name=None)), page_size=10_000)
        cur.execute("""
            UPDATE households h SET turf_id = b.turf_id
            FROM _turf_backfill b WHERE h.id = b.household_id
        """)
        n_turf = cur.rowcount

        n_fac = 0
        if not facilities.empty:
            cur.execute("CREATE TEMP TABLE _fac_backfill "
                        "(household_id uuid, turf_id integer) ON COMMIT DROP")
            rows = list(facilities[["household_uuid", "nearest_turf_id"]]
                        .itertuples(index=False, name=None))
            psycopg2.extras.execute_values(
                cur, "INSERT INTO _fac_backfill VALUES %s", rows, page_size=10_000)
            cur.execute("""
                UPDATE households h SET is_facility = true, turf_id = f.turf_id
                FROM _fac_backfill f WHERE h.id = f.household_id
            """)
            n_fac = cur.rowcount
        return n_turf, n_fac


def write(turfs: pd.DataFrame, assignment: pd.DataFrame, facilities: pd.DataFrame,
         model: str, persons_path: Path, conn) -> None:
    assert_tables_exist(conn)
    check_arms(turfs)
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
        # truncate anything else that ever comes to reference turfs). facilities
        # joined this list with migration 020 for exactly that reason -- it FKs
        # turfs via nearest_turf_id.
        cur.execute("TRUNCATE turf_assignment, facilities, turfs")

        turfs = turfs.copy()
        # pyarrow round-trips a list column as numpy.ndarray per cell, not a
        # native Python list; psycopg2 only knows how to adapt the latter for
        # a text[] column.
        turfs["ed_keys_touched"] = turfs["ed_keys_touched"].apply(
            lambda a: list(a) if a is not None else [])

        # NaN -> None so a missing ratio lands as SQL NULL rather than the float
        # 'NaN' Postgres would otherwise store in a double precision column.
        turfs = turfs.replace({np.nan: None})

        turf_rows = list(turfs[["turf_id", "n_doors", "n_targets", "targets_per_door",
                                "value_dem_ballots", "value_net_margin",
                                "diameter_m", "doors_per_km", "canvasser_hours",
                                "hours_per_ballot", "hours_per_net_margin",
                                "county", "ed_keys_touched", "n_facilities_nearby",
                                "arm"]].itertuples(index=False, name=None))
        turf_rows = [(*r, model) for r in turf_rows]
        psycopg2.extras.execute_values(
            cur,
            "INSERT INTO turfs (turf_id, n_doors, n_targets, targets_per_door, "
            "value_dem_ballots, value_net_margin, diameter_m, doors_per_km, "
            "canvasser_hours, hours_per_ballot, hours_per_net_margin, county, "
            "ed_keys_touched, n_facilities_nearby, arm, model) VALUES %s",
            turf_rows, page_size=5_000)

        if not facilities.empty:
            fac = facilities.replace({np.nan: None})
            fac_rows = list(fac[["facility_id", "household_uuid", "n_targets",
                                 "household_size", "value_dem_ballots", "value_net_margin",
                                 "lat", "lon", "county", "ed_key", "nearest_turf_id"]]
                            .itertuples(index=False, name=None))
            fac_rows = [(*r, model) for r in fac_rows]
            psycopg2.extras.execute_values(
                cur,
                "INSERT INTO facilities (facility_id, household_id, n_targets, "
                "household_size, value_dem_ballots, value_net_margin, lat, lon, "
                "county, ed_key, nearest_turf_id, model) VALUES %s",
                fac_rows, page_size=5_000)

        assign_rows = list(assignment[["person_uuid", "hh_id", "turf_id", "m_i", "m_net_i"]]
                          .itertuples(index=False, name=None))
        psycopg2.extras.execute_values(
            cur,
            "INSERT INTO turf_assignment (person_id, hh_id, turf_id, m_i, m_net_i) VALUES %s",
            assign_rows, page_size=10_000)

    n_turf, n_fac = backfill_household_turf_id(assignment, facilities, persons_path, conn)
    print(f"  households.turf_id backfilled: {n_turf:,} rows")
    print(f"  households.is_facility set: {n_fac:,} rows")
    conn.commit()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--turfs", type=Path, default=C.TURFS_PARQUET)
    ap.add_argument("--assignment", type=Path, default=C.TURF_ASSIGNMENT_PARQUET)
    ap.add_argument("--facilities", type=Path, default=C.FACILITIES_PARQUET)
    ap.add_argument("--persons", type=Path, default=C.PERSONS_PARQUET)
    ap.add_argument("--model", default="catboost")
    ap.add_argument("--write", action="store_true",
                    help="actually TRUNCATE + INSERT; without it this is a dry run")
    args = ap.parse_args()

    print("Loading turfs / turf_assignment / facilities parquet...")
    turfs, assignment = load_frames(args.turfs, args.assignment, args.persons)
    facilities = load_facilities(args.facilities, args.persons)
    report(turfs, assignment, facilities)
    # Same gate the write path runs, brought forward so a dry run surfaces it too.
    check_arms(turfs)

    n_households = len(household_turf_map(assignment, args.persons))

    if not args.write:
        print(f"\n[dry run] would TRUNCATE + INSERT `turfs` ({len(turfs):,} rows), "
              f"`turf_assignment` ({len(assignment):,} rows) and `facilities` "
              f"({len(facilities):,} rows), and backfill households.turf_id for "
              f"{n_households:,} households ({len(facilities):,} of them flagged "
              f"is_facility). Re-run with --write to apply.")
        return

    print("\nWriting to Supabase...")
    conn = connect()
    try:
        write(turfs, assignment, facilities, args.model, args.persons, conn)
    finally:
        conn.close()
    print("  done.")


if __name__ == "__main__":
    main()
