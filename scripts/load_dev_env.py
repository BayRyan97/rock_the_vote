#!/usr/bin/env python3
"""load_dev_env.py -- populate the dev or preview Supabase project from the cache.

Task S-04 in docs/canvass_task_plan.md.

WHY THIS EXISTS. Claude Code has to be able to run, query and test the app end
to end, and the spec's targeting behaviour is only meaningful against real
turfs -- synthetic fixtures cannot tell you that moving the lean floor changes
the door count. The alternative to this script is developing against
production, which is how a stray migration or a bad write-back takes the live
database down mid-cycle.

WHAT IT LOADS. Not the whole database: a SUBSET, keyed on a handful of turfs.
That is a privacy decision as much as a speed one. Production covers ~1.85M
people; a dev project seeded with eight turfs holds a few thousand. Both are
real, so behaviour is faithful, but the second cloud project's blast radius is
three orders of magnitude smaller.

SOURCE. The local Parquet cache (config.CACHE), not the production database --
minutes rather than hours, no load on the live instance, and boe_contacts is
already stripped of email, phone, names and street address there.

NEVER LOADED: `profiles` and anything under `auth`. Those rows reference
auth.users in THEIR OWN project; copying them across would point at user ids
that do not exist there. Sign up again in the dev project instead.

Usage:
    python scripts/load_dev_env.py --target dev --dry-run
    python scripts/load_dev_env.py --target dev --turfs 8 --yes
"""
import argparse
import sys
from pathlib import Path

import psycopg2
import psycopg2.extras
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "model"))
import config as C  # noqa: E402
import db as D  # noqa: E402

BATCH = 5_000
DEFAULT_TURFS = 8

# Loaded parents-first; truncated in reverse. `profiles` is absent by design
# (see the module docstring), and so is canvass_notes -- field observations are
# operational, and an empty notes table is the honest starting state for a dev
# environment.
LOAD_ORDER = [
    "turfs", "households", "people", "turf_assignment",
    "ev_scores", "election_results", "donations_meta",
    "donations", "donation_summaries", "boe_contacts",
]
NEVER_LOAD = {"profiles", "users", "auth.users"}


def cache_path(table):
    return C.CACHE / (table + ".parquet")


def read_subset(table, column=None, keys=None):
    """Read a cached table, keeping only rows whose `column` is in `keys`.

    The predicate is pushed into pyarrow so row groups that cannot match are
    skipped: people.parquet is 1.85M rows and donations.parquet 3.7M, and the
    whole point is to come away with a few thousand of each.
    """
    path = cache_path(table)
    if not path.exists():
        return None
    if column is None or keys is None:
        return pq.read_table(path)
    return pq.read_table(path, filters=[(column, "in", list(keys))])


def target_columns(cur, table):
    """The columns the destination actually has.

    Intersecting against this rather than trusting the cache is what keeps a
    stale cache loadable: the cached households snapshot predates turf_id,
    is_facility and legislative_district, and a straight column-for-column
    insert would fail on the first row.
    """
    cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = %s", (table,))
    return [r[0] for r in cur.fetchall()]


def insert_table(cur, table, arrow_tbl):
    available = set(target_columns(cur, table))
    cols = [c for c in arrow_tbl.schema.names if c in available]
    if not cols:
        return 0
    rows = arrow_tbl.select(cols).to_pylist()
    values = [tuple(r[c] for c in cols) for r in rows]
    if not values:
        return 0
    sql = "INSERT INTO {} ({}) VALUES %s ON CONFLICT DO NOTHING".format(
        table, ", ".join('"' + c + '"' for c in cols))
    for i in range(0, len(values), BATCH):
        psycopg2.extras.execute_values(cur, sql, values[i:i + BATCH], page_size=BATCH)
    return len(values)


def preflight(cur):
    """Warn about sessions stuck idle in a transaction before writing.

    A week-old idle-in-transaction session once starved the production instance
    until every query queued behind it. TRUNCATE takes an ACCESS EXCLUSIVE lock,
    so the same thing here does not slow the load down, it hangs it -- and an
    unexplained hang invites exactly the wrong reaction, which is to point the
    script at a database that IS responding.
    """
    cur.execute(
        "SELECT count(*) FROM pg_stat_activity WHERE state = 'idle in transaction' "
        "AND xact_start < now() - interval '5 minutes'")
    stuck = cur.fetchone()[0]
    if stuck:
        print("  WARNING: {} session(s) idle in transaction for over 5 minutes.".format(stuck))
        print("  TRUNCATE will queue behind them. Check pg_stat_activity before retrying.")


def select_turfs(turf_ids, n):
    """Pick the turfs to seed, deterministically.

    Sorted, so two runs with the same -n produce the same dev environment and
    two people can compare door counts without first having to establish that
    they are even looking at the same turfs.
    """
    ordered = sorted(turf_ids)
    if n is None or n >= len(ordered):
        return ordered
    return ordered[:n]


def build_subset(turf_ids, n):
    """Resolve the turf selection into one Arrow table per destination table."""
    turfs = read_subset("turfs")
    if turfs is None:
        raise SystemExit("{} is missing; refresh the cache.".format(cache_path("turfs")))
    wanted = turf_ids if turf_ids else select_turfs(turfs.column("turf_id").to_pylist(), n)
    turfs = turfs.filter(pc.is_in(turfs.column("turf_id"), value_set=pa.array(wanted)))

    wanted_set = set(wanted)
    assign = read_subset("turf_assignment", "turf_id", wanted_set)

    # turf_assignment.hh_id is NOT households.id. It is an integer row index
    # from the model pipeline (migration 017 declares it `integer NOT NULL`
    # with no foreign key), while households.id is a uuid. The only honest path
    # from a turf to a household is through the person:
    #     turf -> turf_assignment.person_id -> people.household_id -> households.id
    # Joining on hh_id instead does not fail loudly, it silently matches
    # nothing, and a dev environment with zero households looks like a broken
    # loader rather than a wrong key.
    person_ids = set(assign.column("person_id").to_pylist()) if assign is not None else set()
    seed_people = read_subset("people", "id", person_ids)
    hh_uuids = set(seed_people.column("household_id").to_pylist()) if seed_people is not None else set()

    # Re-read on household_id, so a seeded household arrives with ALL of its
    # members, not only the ones the targeting pass happened to assign.
    # households.people_count is a stored column; loading a subset of a
    # household's people would contradict it on the very first page that
    # renders both.
    people = read_subset("people", "household_id", hh_uuids)
    donor_keys = set()
    voter_ids = set()
    if people is not None:
        donor_keys = {k for k in people.column("donor_key").to_pylist() if k}
        voter_ids = {v for v in people.column("voter_id").to_pylist() if v}

    return wanted_set, {
        "turfs": turfs,
        "households": read_subset("households", "id", hh_uuids),
        "people": people,
        "turf_assignment": assign,
        "ev_scores": read_subset("ev_scores"),
        "election_results": read_subset("election_results"),
        "donations_meta": read_subset("donations_meta"),
        "donations": read_subset("donations", "donor_key", donor_keys),
        "donation_summaries": read_subset("donation_summaries", "donor_key", donor_keys),
        "boe_contacts": read_subset("boe_contacts", "voter_id", voter_ids),
    }


def load(url, subset):
    """TRUNCATE and repopulate, in one transaction."""
    conn = psycopg2.connect(url)
    try:
        conn.autocommit = False
        with conn.cursor() as cur:
            preflight(cur)
            present = [t for t in LOAD_ORDER
                       if subset.get(t) is not None and t not in NEVER_LOAD]
            # CASCADE also empties anything referencing these (canvass_notes).
            # Stated rather than discovered: in a dev project that is the
            # intent, but it should never come as a surprise.
            cur.execute("TRUNCATE {} RESTART IDENTITY CASCADE".format(", ".join(present)))
            print("  truncated {} table(s) (CASCADE: anything referencing them, "
                  "e.g. canvass_notes, is emptied too)".format(len(present)))
            for t in present:
                print("  {:20s} {:>9,} rows inserted".format(t, insert_table(cur, t, subset[t])))
            # The cached households snapshot predates households.turf_id, so
            # derive it from the assignment table -- /api/households/[id]/notes
            # reads that column directly. Through people.household_id, for the
            # reason spelled out in build_subset: a.hh_id is not h.id.
            if "turf_id" in target_columns(cur, "households"):
                cur.execute(
                    "UPDATE households h SET turf_id = a.turf_id "
                    "FROM turf_assignment a JOIN people p ON p.id = a.person_id "
                    "WHERE p.household_id = h.id AND h.turf_id IS DISTINCT FROM a.turf_id")
                print("  households.turf_id   {:>9,} rows backfilled".format(cur.rowcount))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", default="dev", choices=["dev", "preview"],
                    help="which Supabase project to populate (never prod)")
    ap.add_argument("--turfs", type=int, default=DEFAULT_TURFS,
                    help="how many turfs to seed (default {}; 0 = all)".format(DEFAULT_TURFS))
    ap.add_argument("--turf-ids", type=int, nargs="+",
                    help="seed these specific turf ids instead of the first N")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would be loaded; touches no database")
    ap.add_argument("--yes", action="store_true",
                    help="required to actually write (TRUNCATE + INSERT)")
    args = ap.parse_args()

    if not C.CACHE.exists():
        raise SystemExit(
            "no Parquet cache at {}. Run `python model/refresh_cache.py` against "
            "production first -- it is read-only there.".format(C.CACHE))

    wanted, subset = build_subset(args.turf_ids, args.turfs or None)

    shown = ", ".join(str(t) for t in sorted(wanted)[:12])
    print("Seeding from {} turf(s): {}{}".format(
        len(wanted), shown, " ..." if len(wanted) > 12 else ""))
    for name in LOAD_ORDER:
        tbl = subset.get(name)
        print("  {:20s} {:>9,} rows{}".format(
            name, 0 if tbl is None else tbl.num_rows,
            "   (not in cache -- skipped)" if tbl is None else ""))

    if args.dry_run:
        print("\n--dry-run: no database was contacted.")
        return 0

    name, url = D.target_url(args.target)
    D.assert_not_prod(url, "load the {} environment".format(name))
    ref = D.project_ref(url)
    if not args.yes:
        print("\nWould TRUNCATE and repopulate {} tables in project {} ({}). "
              "Re-run with --yes to proceed.".format(len(LOAD_ORDER), ref, name))
        return 0

    print("\nLoading into project {} ({}) ...".format(ref, name))
    load(url, subset)
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
