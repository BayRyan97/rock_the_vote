"""test_persons_io.py — self-checks for stamped side files and feature vintages.

features_history writes two files with IDENTICAL feature columns: as-of
TARGET_GENERAL_YEAR for training, as-of SERVE_GENERAL_YEAR for scoring. That
sameness is the point — a model trained on one can score the other — and it is
also the hazard, because training on the serving vintage would put the outcome
inside the features and nothing in the data distinguishes them. The vintage
stamp is the only thing that does.

Run:  python model/test_persons_io.py     (exit 0 = all checks pass)
"""
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config as C  # noqa: E402
from persons_io import (FINGERPRINT_KEY, population_fingerprint,  # noqa: E402
                        read_stamp, write_stamped)

FAILURES = []
TMP = Path(tempfile.mkdtemp())


def ok(name, got, want):
    good = got == want
    if not good:
        FAILURES.append(f"{name}: got {got!r}, want {want!r}")
    print(f"  [{'OK' if good else 'FAIL'}] {name}" + ("" if good else f"  got={got!r}"))


print("test_persons_io")
print(" A. stamps round-trip through parquet metadata")
df = pd.DataFrame({"person_id": np.arange(3), "v": [1.0, 2.0, 3.0]})
p = TMP / "stamped.parquet"
write_stamped(df, p, "abc123", history_target_year=2024, other="x")
ok("fingerprint", read_stamp(p, FINGERPRINT_KEY.decode()), "abc123")
ok("extra metadata", read_stamp(p, "history_target_year"), "2024")
ok("second extra key", read_stamp(p, "other"), "x")
ok("absent key is None", read_stamp(p, "nope"), None)
ok("data survives", pd.read_parquet(p)["v"].tolist(), [1.0, 2.0, 3.0])

plain = TMP / "plain.parquet"
df.to_parquet(plain, index=False)
ok("unstamped file reads None", read_stamp(plain, "history_target_year"), None)

print(" B. population_fingerprint identifies WHICH people, in order")
a = pd.DataFrame({"person_uuid": ["u0", "u1", "u2"]})
b = pd.DataFrame({"person_uuid": ["u0", "u1", "u9"]})     # same size, different people
c = pd.DataFrame({"person_uuid": ["u1", "u0", "u2"]})     # same people, reordered
ok("same table -> same fingerprint",
   population_fingerprint(a), population_fingerprint(a.copy()))
ok("different population -> different", population_fingerprint(a) != population_fingerprint(b), True)
ok("reordered -> different", population_fingerprint(a) != population_fingerprint(c), True)
try:
    population_fingerprint(pd.DataFrame({"person_id": [0, 1]}))
    FAILURES.append("missing person_uuid did not raise")
    print("  [FAIL] missing person_uuid did not raise")
except KeyError as e:
    print(f"  [OK] missing person_uuid raises: {str(e)[:56]}")

print(" C. the two shipped vintages are interchangeable as features")
tr, sv = C.HISTORY_FEATURES_PARQUET, C.HISTORY_SERVE_PARQUET
if not (tr.exists() and sv.exists()):
    print("  [skip] history files not built; run model/features_history.py")
else:
    t_cols = [c for c in pq.read_schema(tr).names if not c.startswith("y_voted_general_")]
    s_cols = [c for c in pq.read_schema(sv).names if not c.startswith("y_voted_general_")]
    ok("identical feature columns", t_cols, s_cols)
    ok("training vintage is TARGET_GENERAL_YEAR",
       read_stamp(tr, "history_target_year"), str(C.TARGET_GENERAL_YEAR))
    ok("serving vintage is SERVE_GENERAL_YEAR",
       read_stamp(sv, "history_target_year"), str(C.SERVE_GENERAL_YEAR))
    ok("both describe the same population",
       read_stamp(tr, FINGERPRINT_KEY.decode()) == read_stamp(sv, FINGERPRINT_KEY.decode()),
       True)
    # The serving vintage must be strictly LATER, or it is not a serving vintage.
    ok("serve year is after target year",
       int(read_stamp(sv, "history_target_year")) > int(read_stamp(tr, "history_target_year")),
       True)
    # And it must actually differ in content, otherwise the split bought nothing.
    h_t = pd.read_parquet(tr, columns=["hist_n_generals"])["hist_n_generals"]
    h_s = pd.read_parquet(sv, columns=["hist_n_generals"])["hist_n_generals"]
    ok("serving vintage has at least as much history for everyone",
       bool((h_s.to_numpy() >= h_t.to_numpy()).all()), True)
    ok("and strictly more for some", bool((h_s.to_numpy() > h_t.to_numpy()).any()), True)

print(" D. the training guard rejects the serving vintage")
from baseline_catboost import assert_training_vintage  # noqa: E402

if sv.exists():
    try:
        assert_training_vintage(sv)
        FAILURES.append("serving vintage accepted for training")
        print("  [FAIL] serving vintage accepted for training")
    except SystemExit as e:
        print(f"  [OK] serving vintage refused: {str(e)[:64]}")
if tr.exists():
    try:
        assert_training_vintage(tr)
        print("  [OK] training vintage accepted")
    except SystemExit as e:
        FAILURES.append(f"training vintage refused: {e}")
        print(f"  [FAIL] training vintage refused: {e}")
# an unstamped legacy file must not be blocked -- it predates the stamp
try:
    assert_training_vintage(plain)
    print("  [OK] unstamped legacy file is not blocked")
except SystemExit as e:
    FAILURES.append(f"unstamped file blocked: {e}")
    print(f"  [FAIL] unstamped file blocked: {e}")

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S):")
    for f in FAILURES:
        print(f"  - {f}")
    sys.exit(1)
print("ALL CHECKS PASSED")
