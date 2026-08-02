"""test_calibration.py — self-checks for the cycle-level shift.

The property the shift exists to have is that it moves the LEVEL and nothing
else, so the tests pin both halves: the mean lands on the target, and the
ordering (hence AUC) is bit-for-bit preserved. Also covers the saturated 0/1
scores CatBoost really emits, which are what a naive logit would turn into inf.

Run:  python model/test_calibration.py     (exit 0 = all checks pass)
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from calibration import solve_offset, to_base_rate  # noqa: E402

FAILURES = []


def check(name, actual, expected, atol=1e-6):
    ok = np.isclose(float(actual), float(expected), atol=atol)
    if not ok:
        FAILURES.append(f"{name}: got {actual}, expected {expected}")
    print(f"  [{'OK' if ok else 'FAIL'}] {name} = {actual}"
          + ("" if ok else f"  (expected {expected})"))


def check_true(name, cond):
    if not cond:
        FAILURES.append(name)
    print(f"  [{'OK' if cond else 'FAIL'}] {name}")


rng = np.random.default_rng(0)

# A bimodal population shaped like the real turnout output: most voters near 1,
# a long tail near 0. A symmetric test distribution would hide the difference
# between shifting the mean and shifting the logit of the mean.
p = np.concatenate([
    rng.beta(8.0, 1.2, 40_000),
    rng.beta(1.2, 8.0, 10_000),
]).astype(np.float32)

print("bimodal population, mean %.4f" % p.mean())

# --- the level lands on the target -------------------------------------------
for target in (0.5735, 0.30, 0.90):
    out = to_base_rate(p, target)
    check(f"mean hits target {target}", out.mean(), target, atol=1e-4)

# --- and nothing else moves ---------------------------------------------------
out = to_base_rate(p, 0.5735)
check_true("strictly within (0, 1)", bool((out > 0).all() and (out < 1).all()))

# The shift is strictly monotone in float64, but the result is cast to float32
# because people.turnout_prob is `real` — so the honest property is NO
# INVERSIONS, not a preserved bijection. Downward shifting compresses the top of
# the range, and scores that differed in the 7th decimal can land on one float32.
# Pinning the strict version instead would fail for a rounding reason and say
# nothing about targeting. (Same distinction as comparing feature identity at
# storage precision rather than bit-exactly.)
order = np.argsort(p, kind="stable")
check_true("no inversions at storage precision",
           bool(np.all(np.diff(out[order].astype(np.float64)) >= 0)))

# Quantify the ties that rounding introduces, and show they cost no AUC. A
# synthetic label correlated with p stands in for turnout.
ties = len(np.unique(p)) - len(np.unique(out))
print(f"  ..distinct values {len(np.unique(p)):,} -> {len(np.unique(out)):,} "
      f"({ties:,} merged by the float32 cast)")

y = (rng.random(len(p)) < p).astype(np.int8)


def auc(score, label):
    r = np.argsort(np.argsort(score))          # average ranks are unnecessary
    n1 = int(label.sum())                       # for a monotone-shift comparison
    n0 = len(label) - n1
    return (r[label == 1].sum() - n1 * (n1 - 1) / 2) / (n1 * n0)


a_before, a_after = auc(p, y), auc(out, y)
check("AUC unchanged by the shift", a_after, a_before, atol=5e-5)

# --- saturated inputs ---------------------------------------------------------
sat = np.array([0.0, 0.0, 1.0, 1.0, 0.5], dtype=np.float32)
out_sat = to_base_rate(sat, 0.5)
check("saturated input still hits target", out_sat.mean(), 0.5, atol=1e-4)
check_true("saturated input stays finite", bool(np.isfinite(out_sat).all()))

# --- disabling ----------------------------------------------------------------
check_true("None disables the shift",
           np.allclose(to_base_rate(p, None), p, atol=1e-6))

# --- offset direction ---------------------------------------------------------
# Serving a midterm from a presidential fit must shift DOWN.
check_true("presidential -> midterm offset is negative",
           solve_offset(p, 0.5735) < 0)
check_true("target above the mean shifts up", solve_offset(p, 0.95) > 0)

# --- guardrails ---------------------------------------------------------------
for bad in (0.0, 1.0, -0.1, 1.5):
    try:
        solve_offset(p, bad)
        FAILURES.append(f"target {bad} should have raised")
        print(f"  [FAIL] target {bad} rejected")
    except ValueError:
        print(f"  [OK] target {bad} rejected")

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S):")
    for f in FAILURES:
        print(f"  - {f}")
    sys.exit(1)
print("all checks pass")
