"""test_catboost_util.py — self-checks for categorical rendering.

The category STRING is the feature: '282' and '282.0' are different levels to
CatBoost, and CatBoost matches features by name, not by category values, so a
rendering change scores without error against levels the model never saw.
These pin the rules that keep a level stable across dtypes, across parquet
round-trips, and across batches.

Run:  python model/test_catboost_util.py     (exit 0 = all checks pass)
"""
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from catboost_util import (MISSING_CATEGORY, PREP_CONTRACT,  # noqa: E402
                           check_no_exact_recovery,
                           as_category, manifest_spec, prepare, validate_spec)

FAILURES = []


def ok(name, got, want):
    good = list(got) == list(want)
    if not good:
        FAILURES.append(f"{name}: got {list(got)}, want {list(want)}")
    print(f"  [{'OK' if good else 'FAIL'}] {name}" + ("" if good else f"  got={list(got)}"))


def raises(name, fn, mention=(), exc=ValueError):
    try:
        fn()
    except exc as e:
        miss = [m for m in mention if m not in str(e)]
        if miss:
            FAILURES.append(f"{name}: message lacks {miss}")
        print(f"  [{'OK' if not miss else 'FAIL'}] {name}: {str(e)[:76]}")
        return
    FAILURES.append(f"{name}: did not raise")
    print(f"  [FAIL] {name} did not raise")


print("test_catboost_util")
print(" A. every representation of the same identifier converges")
# The reported regression: one NULL row flips int16 -> float64, and the old
# renderer turned '282' into '282.0' for EVERY row of the column.
ok("int16", as_category(pd.Series([282, 17], dtype="int16"), "integer_id"), ["282", "17"])
ok("float64", as_category(pd.Series([282.0, 17.0], dtype="float64"), "integer_id"), ["282", "17"])
ok("nullable Float64", as_category(pd.Series([282.0, pd.NA], dtype="Float64"), "integer_id"),
   ["282", MISSING_CATEGORY])
ok("nullable Int16", as_category(pd.Series([282, None], dtype="Int16"), "integer_id"),
   ["282", MISSING_CATEGORY])
ok("numeric as object", as_category(pd.Series([282.0, 17.0], dtype=object), "integer_id"),
   ["282", "17"])
ok("numeric-looking string", as_category(pd.Series(["282.0", "17"]), "integer_id"), ["282", "17"])

print(" B. the value alone decides the category, not the batch it arrived in")
lone = as_category(pd.Series([282.0, 17.0]), "integer_id")[0]
other = as_category(pd.Series([282.0, 999.0, 4.0]), "integer_id")[0]
ok("same token across batches", [lone], [other])

print(" C. invalid identifiers raise instead of being rounded into a neighbour")
raises("fractional", lambda: as_category(pd.Series([282.0, 1.5]), "integer_id", "assembly_district"),
       mention=["fractional", "assembly_district"])
raises("near-integer 282.0001", lambda: as_category(pd.Series([282.0001]), "integer_id", "sd"),
       mention=["fractional"])
raises("near-integer 11797.05", lambda: as_category(pd.Series([11797.05]), "zip5", "zip_code"),
       mention=["fractional"])
raises("infinity", lambda: as_category(pd.Series([np.inf, 1.0]), "integer_id", "cd"),
       mention=["finite"])
raises("non-numeric", lambda: as_category(pd.Series(["12A"]), "integer_id", "cd"),
       mention=["not numeric", "12A"])
raises("unknown format", lambda: as_category(pd.Series([1]), "postcode", "x"),
       mention=["unknown manifest format"])

print(" D. missing has one spelling, whatever the dtype")
for nm, s in (("float64", pd.Series([1.0, np.nan])),
              ("object", pd.Series(["a", None], dtype=object)),
              ("Int16", pd.Series([1, None], dtype="Int16")),
              ("string", pd.Series(["a", pd.NA], dtype="string"))):
    ok(f"missing from {nm}", as_category(s)[1:], [MISSING_CATEGORY])
ok("all-missing column", as_category(pd.Series([None, None], dtype=object), "integer_id"),
   [MISSING_CATEGORY] * 2)

# object columns survive parquet as None, not pd.NA — the round trip used to
# change the token from '<NA>' to 'None'.
_tmp = Path(tempfile.mkdtemp()) / "rt.parquet"
pd.DataFrame({"c": pd.Series(["a", pd.NA], dtype=object)}).to_parquet(_tmp, index=False)
ok("missing survives parquet round-trip",
   as_category(pd.read_parquet(_tmp)["c"])[1:], [MISSING_CATEGORY])

print(" E. free-text categoricals keep their value verbatim")
ok("literal 'NA' is not the sentinel", as_category(pd.Series(["GLEN COVE", "NA", None])),
   ["GLEN COVE", "NA", MISSING_CATEGORY])
ok("genuine fractions survive as text", as_category(pd.Series([1.5, 2.25])), ["1.5", "2.25"])

print(" F. zip5 zero-pads and range-checks")
ok("zero-pad", as_category(pd.Series([1234, 11797]), "zip5"), ["01234", "11797"])
raises("out of range", lambda: as_category(pd.Series([123456]), "zip5", "zip_code"),
       mention=["00000-99999"])

print(" G. prepare() contract")
src = pd.DataFrame({"age": [30, 40], "zip_code": [11797.0, 1234.0],
                    "county": ["NASSAU", None]})
X = prepare(src, ["age"], ["zip_code", "county"])
ok("column order is numeric + categorical", X.columns, ["age", "zip_code", "county"])
ok("manifest format applied to zip_code", X["zip_code"], ["11797", "01234"])
ok("free-text county untouched", X["county"], ["NASSAU", MISSING_CATEGORY])
ok("caller's frame not mutated", src["zip_code"], [11797.0, 1234.0])
ok("aliases resolve to manifest formats",
   prepare(src.rename(columns={"zip_code": "zip"}), ["age"], ["zip"],
           aliases={"zip_code": "zip"})["zip"], ["11797", "01234"])

print(" H. the preprocessing contract is a non-empty version string")
ok("PREP_CONTRACT set", [bool(PREP_CONTRACT) and isinstance(PREP_CONTRACT, str)], [True])

print(" I. the manifest's spans_cutoff invariant, checked on read")
# spans_cutoff features summarise history THROUGH the export date, so they carry
# the target election's outcome. gtn.py feeds the encoder into the turnout head,
# so "encoder" is as disqualifying as "turnout_head".
ok("the shipped manifest is valid", [validate_spec(manifest_spec())], [None])
for usage in (["encoder"], ["turnout_head"], ["encoder", "party_head"]):
    raises(f"spans_cutoff + {usage} rejected",
           lambda u=usage: validate_spec({"x": {"spans_cutoff": True, "usage": u}}),
           mention=["spans_cutoff", "turnout task"], exc=AssertionError)
ok("spans_cutoff + party_head allowed",
   [validate_spec({"x": {"spans_cutoff": True, "usage": ["party_head"]}})], [None])
ok("no spans_cutoff at all is fine",
   [validate_spec({"x": {"usage": ["encoder"]}})], [None])
# every spans_cutoff feature in the real manifest is party_head-only
_sc = {n: m["usage"] for n, m in manifest_spec().items() if m.get("spans_cutoff")}
ok("real spans_cutoff features are party_head-only",
   sorted({u for us in _sc.values() for u in us}), ["party_head"])

print(" J. check_no_exact_recovery finds real identities and only those")
_rng = np.random.default_rng(7)
_N = 5000


def recovery(df, visible, withheld):
    """Return the hit list, or [] — turns the assertion into a value."""
    try:
        check_no_exact_recovery(df, visible, withheld, "t")
        return []
    except AssertionError as e:
        return sorted(str(e).split("visible ones: ")[1].strip("[]").replace("'", "").split(", "))


# The integer identity the guard was written for.
_g = _rng.integers(0, 12, _N).astype(np.int16)
_p = _rng.integers(0, 6, _N).astype(np.int16)
_df = pd.DataFrame({"hist_n_generals": _g, "hist_n_primaries": _p,
                    "hist_n_votes": (_g + _p).astype(np.int16),
                    "age": _rng.integers(18, 90, _N)})
ok("integer identity found",
   recovery(_df, ["hist_n_generals", "hist_n_votes", "age"], ["hist_n_primaries"]),
   ["hist_n_primaries == hist_n_votes - hist_n_generals"])

# A float32-STORED identity. The old byte-exact check missed this entirely
# because float64(a) - float64(w) is not bit-equal to the float32-rounded b.
_a = _rng.random(_N).astype(np.float32)
_w = _rng.random(_N).astype(np.float32)
_df = pd.DataFrame({"rate_a": _a, "rate_b": (_a - _w).astype(np.float32),
                    "noise": _rng.random(_N).astype(np.float32), "hidden_rate": _w})
_V = {c: _df[c].to_numpy(np.float64) for c in ("rate_a", "rate_b", "noise")}
_sigs = {v.tobytes(): c for c, v in _V.items()}
_wv = _df["hidden_rate"].to_numpy(np.float64)
ok("old byte-exact check would miss it",
   [c for a, av in _V.items() for c in [_sigs.get((av - _wv).tobytes())] if c and c != a], [])
ok("float32-stored identity found",
   recovery(_df, ["rate_a", "rate_b", "noise"], ["hidden_rate"]),
   ["hidden_rate == rate_a - rate_b"])

# All-zero columns: a smoke subset with no matched donors used to raise here.
_df = pd.DataFrame({"has_donation": np.zeros(_N), "fec_n": np.zeros(_N),
                    "nyboe_n": np.zeros(_N), "age": _rng.integers(18, 90, _N),
                    "hist_n_primaries": np.zeros(_N)})
ok("constant columns raise nothing",
   recovery(_df, ["has_donation", "fec_n", "nyboe_n", "age"], ["hist_n_primaries"]), [])

# Unrelated noise must not be flagged.
_df = pd.DataFrame({c: _rng.random(_N) for c in ("x", "y", "z")})
_df["w"] = _rng.random(_N)
ok("no spurious hits on noise", recovery(_df, ["x", "y", "z"], ["w"]), [])

# The w == b - a direction, covered by iterating ordered pairs.
_x, _y = _rng.random(_N), _rng.random(_N)
_df = pd.DataFrame({"x": _x, "sum_xy": _x + _y, "hidden_y": _y, "pad": _rng.random(_N)})
ok("reversed direction found",
   recovery(_df, ["x", "sum_xy", "pad"], ["hidden_y"]), ["hidden_y == sum_xy - x"])

# A NaN-bearing visible column must not hide a finite identity.
_a, _b = _rng.random(_N), _rng.random(_N)
_nan = _a.copy(); _nan[:50] = np.nan
_df = pd.DataFrame({"a": _a, "b": _b, "nanny": _nan, "hidden": _a - _b})
ok("identity found alongside a NaN column",
   recovery(_df, ["a", "b", "nanny"], ["hidden"]), ["hidden == a - b"])

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S):")
    for f in FAILURES:
        print(f"  - {f}")
    sys.exit(1)
print("ALL CHECKS PASSED")
