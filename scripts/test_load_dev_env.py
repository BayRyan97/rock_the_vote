"""test_load_dev_env.py -- self-checks for the dev/preview environment loader.

Three things here can do real damage if they drift, so they get pinned:

  1. The table allowlist. `profiles` rows reference auth.users in their own
     project; loading them into another one produces sessions pointing at user
     ids that do not exist.
  2. The column intersection. The cache is a snapshot and the schema moves;
     inserting a column the destination does not have fails the whole load,
     and inserting one it has under a different meaning is worse.
  3. Turf selection determinism. A dev environment nobody can reproduce is a
     dev environment where two people compare different door counts and
     conclude the targeting code is wrong.

No database is contacted: the cursor is a stub that records what it was asked
to execute.

Run:  python scripts/test_load_dev_env.py     (exit 0 = all checks pass)
"""
import sys
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).resolve().parent))

import load_dev_env as L  # noqa: E402

FAILURES = []


def check(name, got, want):
    if got == want:
        print("  [OK] " + name)
    else:
        FAILURES.append(name + ": got " + repr(got) + ", wanted " + repr(want))
        print("  [FAIL] " + name + ": got " + repr(got) + ", wanted " + repr(want))


class FakeCursor:
    """Records executed SQL and answers information_schema with `columns`."""

    def __init__(self, columns):
        self.columns = columns
        self.executed = []
        self.batches = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchall(self):
        return [(c,) for c in self.columns]


def fake_execute_values(cur, sql, values, page_size=None):
    cur.batches.append((sql, list(values)))


L.psycopg2.extras.execute_values = fake_execute_values


print("-- the table allowlist")
check("profiles is never loaded", "profiles" in L.LOAD_ORDER, False)
check("no auth table is loaded",
      [t for t in L.LOAD_ORDER if t in L.NEVER_LOAD], [])
check("canvass_notes is not seeded", "canvass_notes" in L.LOAD_ORDER, False)
check("turfs precedes turf_assignment (FK order)",
      L.LOAD_ORDER.index("turfs") < L.LOAD_ORDER.index("turf_assignment"), True)
check("households precedes people (FK order)",
      L.LOAD_ORDER.index("households") < L.LOAD_ORDER.index("people"), True)

print()
print("-- select_turfs(): deterministic and bounded")
ids = [7, 3, 91, 12, 1]
check("sorted, first N", L.select_turfs(ids, 3), [1, 3, 7])
check("stable across call order", L.select_turfs(list(reversed(ids)), 3), [1, 3, 7])
check("n larger than the pool returns all", L.select_turfs(ids, 99), [1, 3, 7, 12, 91])
check("n None returns all", L.select_turfs(ids, None), [1, 3, 7, 12, 91])

print()
print("-- insert_table(): columns the destination does not have are dropped")
tbl = pa.table({"id": ["a", "b"], "turf_id": [1, 2], "ghost_col": ["x", "y"]})
cur = FakeCursor(["id", "turf_id", "is_facility"])
n = L.insert_table(cur, "households", tbl)
check("all rows offered", n, 2)
sql = cur.batches[0][0]
check("the stale column is not in the INSERT", "ghost_col" in sql, False)
check("the shared columns are", '"id", "turf_id"' in sql, True)
check("a destination-only column is not invented", "is_facility" in sql, False)
check("conflicts are tolerated (re-runnable)", "ON CONFLICT DO NOTHING" in sql, True)

print()
print("-- insert_table(): nothing in common means nothing written")
cur = FakeCursor(["totally", "different"])
check("no rows", L.insert_table(cur, "households", tbl), 0)
check("no statement issued", cur.batches, [])

print()
print("-- insert_table(): batching")
big = pa.table({"id": [str(i) for i in range(L.BATCH + 7)]})
cur = FakeCursor(["id"])
L.insert_table(cur, "households", big)
check("split into two batches", len(cur.batches), 2)
check("first batch is full", len(cur.batches[0][1]), L.BATCH)
check("remainder in the second", len(cur.batches[1][1]), 7)

print()
print("-- read_subset(): a table missing from the cache is skipped, not fatal")
check("returns None", L.read_subset("no_such_table_xyz"), None)

print()
print("-- the turf -> household key chain")
# turf_assignment.hh_id is an integer row index from the model pipeline;
# households.id is a uuid. Joining them matches nothing AND raises nothing, so
# the symptom is an empty dev environment that looks like a broken loader.
# Migration 017 declares hh_id with no foreign key, which is why no constraint
# catches it either. Both the Arrow subset and the SQL backfill must route
# through people.
src = Path(L.__file__).read_text(encoding="utf-8")
check("build_subset selects people by turf_assignment.person_id",
      'read_subset("people", "id", person_ids)' in src, True)
check("households are selected on the uuid set, not hh_id",
      'read_subset("households", "id", hh_uuids)' in src, True)
check("the turf_id backfill joins through people",
      "JOIN people p ON p.id = a.person_id" in src, True)
check("nothing joins hh_id to households.id",
      "a.hh_id = h.id" in src, False)

print()
if FAILURES:
    print(str(len(FAILURES)) + " FAILURE(S):")
    for f in FAILURES:
        print("  - " + f)
    sys.exit(1)
print("ALL CHECKS PASSED")
