"""test_splits.py — self-checks for split-label loading.

load_split_labels is the single point where a person row acquires its
train/val/test label, and every consumer trusts it blindly: graph_build.py
casts the result to int8 (where NaN would silently become 0, i.e. "train"),
while baseline_catboost.py compares against the three names (where the same
row would silently drop out of all three). These pin the cases that used to
pass through it unnoticed.

Run:  python model/test_splits.py     (exit 0 = all checks pass)
"""
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from splits import VALID_SPLITS, load_split_labels  # noqa: E402

FAILURES = []
TMP = Path(tempfile.mkdtemp())


def write_splits(rows, name) -> Path:
    path = TMP / f"{name}.parquet"
    pd.DataFrame(rows, columns=["ed_key", "split"]).to_parquet(path, index=False)
    return path


def persons_of(ed_keys, index=None) -> pd.DataFrame:
    return pd.DataFrame({"ed_key": ed_keys},
                        index=range(len(ed_keys)) if index is None else index)


def expect_raises(name, fn, must_mention=()):
    try:
        fn()
    except ValueError as e:
        missing = [m for m in must_mention if m not in str(e)]
        ok = not missing
        FAILURES.append(f"{name}: message lacks {missing}") if missing else None
        print(f"  [{'OK' if ok else 'FAIL'}] {name} raised: {str(e)[:88]}")
        return
    FAILURES.append(f"{name}: did not raise")
    print(f"  [FAIL] {name} did not raise")


def expect_ok(name, actual, expected):
    ok = list(actual) == list(expected)
    if not ok:
        FAILURES.append(f"{name}: got {list(actual)}, expected {list(expected)}")
    print(f"  [{'OK' if ok else 'FAIL'}] {name}")


GOOD = [("A|1|001", "train"), ("A|1|002", "val"), ("B|2|001", "test")]

print("test_splits")

# --- healthy path: labels, row order and index all preserved -----------------
good_path = write_splits(GOOD, "good")
p = persons_of(["B|2|001", "A|1|001", "A|1|001", "A|1|002"])
labels = load_split_labels(p, good_path)
expect_ok("healthy labels in row order", labels, ["test", "train", "train", "val"])
expect_ok("healthy preserves index", labels.index, p.index)

# a non-default index must survive too — consumers mask numpy arrays with this
p_odd = persons_of(["A|1|001", "B|2|001"], index=[17, 4])
expect_ok("non-default index preserved", load_split_labels(p_odd, good_path).index, [17, 4])

# --- an ed_key with no assignment -------------------------------------------
expect_raises("absent ed_key raises",
              lambda: load_split_labels(persons_of(["A|1|001", "Z|9|999"]), good_path),
              must_mention=["Z|9|999", "unmatched"])

# --- a null ed_key on the persons side --------------------------------------
expect_raises("null person ed_key raises",
              lambda: load_split_labels(persons_of(["A|1|001", None]), good_path),
              must_mention=["null ed_key"])

# mixed null + unmatched must not crash while BUILDING the message
expect_raises("null + unmatched reports both",
              lambda: load_split_labels(persons_of(["Z|9|999", None]), good_path),
              must_mention=["unmatched", "null ed_key"])

# --- bad label in the split table -------------------------------------------
expect_raises("invalid split label raises",
              lambda: load_split_labels(persons_of(["A|1|001"]),
                                        write_splits(GOOD + [("C|3|001", "validation")],
                                                     "bad_label")),
              must_mention=["validation", "unexpected split label"])

expect_raises("null split label raises",
              lambda: load_split_labels(persons_of(["A|1|001"]),
                                        write_splits(GOOD + [("C|3|001", None)],
                                                     "null_label")),
              must_mention=["unexpected split label"])

# --- malformed split table ---------------------------------------------------
expect_raises("duplicate ed_key raises",
              lambda: load_split_labels(persons_of(["A|1|001"]),
                                        write_splits(GOOD + [("A|1|001", "test")],
                                                     "dupe")),
              must_mention=["A|1|001", "more than once"])

expect_raises("null ed_key in table raises",
              lambda: load_split_labels(persons_of(["A|1|001"]),
                                        write_splits(GOOD + [(None, "test")],
                                                     "null_key")),
              must_mention=["null ed_key"])

# --- the cast graph_build.py performs is safe on a healthy result ------------
ids = load_split_labels(p, good_path).map({"train": 0, "val": 1, "test": 2}).to_numpy(np.int8)
expect_ok("int8 cast has no silent zeros", ids, [2, 0, 0, 1])
expect_ok("VALID_SPLITS matches the cast keys", sorted(VALID_SPLITS), ["test", "train", "val"])

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S):")
    for f in FAILURES:
        print(f"  - {f}")
    sys.exit(1)
print("ALL CHECKS PASSED")
