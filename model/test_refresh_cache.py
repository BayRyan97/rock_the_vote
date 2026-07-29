"""test_refresh_cache.py — self-checks for the atomic cache dump.

The local Parquet cache is the ONLY local copy of the Supabase model tables,
and re-pulling `people` is hours over the wire. Writing straight to the target
path truncates a good file before a single row lands, so an interrupted dump
used to destroy it two ways:

  * a Python-level failure (dropped connection, statement timeout) — pyarrow's
    finalizer writes a footer, leaving a VALID file with fewer rows;
  * a killed process (OOM SIGKILL) — no footer at all, and pd.read_parquet
    rejects it with "Parquet magic bytes not found".

dump_to_parquet takes a `fetch` callable, so both paths are testable without a
database.

Run:  python model/test_refresh_cache.py     (exit 0 = all checks pass)
"""
import sys
import tempfile
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parent))

from refresh_cache import dump_to_parquet  # noqa: E402

FAILURES = []
TMP = Path(tempfile.mkdtemp())
COLS = [("a", "bigint")]
SCHEMA = pa.schema([pa.field("a", pa.int64())])


def ok(name, got, want):
    good = got == want
    if not good:
        FAILURES.append(f"{name}: got {got!r}, want {want!r}")
    print(f"  [{'OK' if good else 'FAIL'}] {name}" + ("" if good else f"  got={got!r}"))


def batches(*chunks):
    """A fetch() that yields each chunk once, then empties."""
    queue = [[(v,) for v in c] for c in chunks]

    def fetch():
        return queue.pop(0) if queue else []
    return fetch


def exploding(*chunks, after):
    """A fetch() that raises after `after` successful chunks."""
    queue = [[(v,) for v in c] for c in chunks]
    state = {"n": 0}

    def fetch():
        if state["n"] >= after:
            raise ConnectionError("simulated connection drop")
        state["n"] += 1
        return queue.pop(0) if queue else []
    return fetch


def seed(path, values):
    pq.write_table(pa.table({"a": values}, schema=SCHEMA), path)
    return path.stat().st_size


print("test_refresh_cache")
print(" A. success replaces the target and leaves no partial")
out = TMP / "t1.parquet"
seed(out, [1, 2, 3])
n = dump_to_parquet(batches([7, 8], [9]), COLS, SCHEMA, out)
ok("rows written", n, 3)
ok("target replaced", pq.read_table(out)["a"].to_pylist(), [7, 8, 9])
ok("no .partial left", (TMP / "t1.parquet.partial").exists(), False)

print(" B. a mid-dump failure leaves the existing cache untouched")
out = TMP / "t2.parquet"
size_before = seed(out, list(range(100)))
before = out.read_bytes()
try:
    dump_to_parquet(exploding([1, 2], [3, 4], after=1), COLS, SCHEMA, out)
    FAILURES.append("B: exception was swallowed")
    print("  [FAIL] exception was swallowed")
except ConnectionError:
    print("  [OK] the exception propagates (not swallowed)")
ok("existing file byte-identical", out.read_bytes(), before)
ok("still readable, same rows", pq.ParquetFile(out).metadata.num_rows, 100)
ok("size unchanged", out.stat().st_size, size_before)
ok("no .partial left", (TMP / "t2.parquet.partial").exists(), False)

print(" C. a failure on the very first fetch is also safe")
out = TMP / "t3.parquet"
before = out, seed(out, [42])
try:
    dump_to_parquet(exploding([1], after=0), COLS, SCHEMA, out)
except ConnectionError:
    pass
ok("existing file survives", pq.read_table(out)["a"].to_pylist(), [42])
ok("no .partial left", (TMP / "t3.parquet.partial").exists(), False)

print(" D. writing where nothing exists yet")
out = TMP / "t4.parquet"
n = dump_to_parquet(batches([5, 6]), COLS, SCHEMA, out)
ok("rows written", n, 2)
ok("file created", pq.read_table(out)["a"].to_pylist(), [5, 6])

print(" E. an empty result set still yields a valid zero-row file")
out = TMP / "t5.parquet"
n = dump_to_parquet(batches(), COLS, SCHEMA, out)
ok("rows written", n, 0)
ok("valid and empty", pq.ParquetFile(out).metadata.num_rows, 0)
ok("schema preserved", pq.read_schema(out).names, ["a"])

print(" F. a stale .partial from an earlier crash does not leak into the result")
out = TMP / "t6.parquet"
seed(out, [1])
(TMP / "t6.parquet.partial").write_bytes(b"garbage from a previous crash")
n = dump_to_parquet(batches([3, 4]), COLS, SCHEMA, out)
ok("rows written", n, 2)
ok("result is the new data only", pq.read_table(out)["a"].to_pylist(), [3, 4])

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S):")
    for f in FAILURES:
        print(f"  - {f}")
    sys.exit(1)
print("ALL CHECKS PASSED")
