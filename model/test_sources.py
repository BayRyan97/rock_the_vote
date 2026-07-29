"""test_sources.py — self-checks for donation payload date handling.

A donation whose date cannot be read is DROPPED, because a record that cannot
be placed relative to the target election would leak post-election giving into
an as-of feature. That makes the parser load-bearing in a quiet way: a format
it cannot read does not raise, it zeroes has_donation / fec_n / fec_total /
fec_recency_days for the whole population behind a plausible log line.

Two producers disagree today — build/fetch_fec_bulk.py emits 'YYYY-MM-DD' (what
the committed dist/*.b64 payloads hold), build/fetch_fec.py stores the FEC API's
raw contribution_receipt_date, an ISO 8601 timestamp — so both must parse.

Run:  python model/test_sources.py     (exit 0 = all checks pass)
"""
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sources import _load_donation_payloads, _parse_record_date  # noqa: E402

FAILURES = []
CUTOFF = date(2024, 11, 5)


def ok(name, got, want):
    good = got == want
    if not good:
        FAILURES.append(f"{name}: got {got!r}, want {want!r}")
    print(f"  [{'OK' if good else 'FAIL'}] {name}" + ("" if good else f"  got={got!r}"))


def raises(name, fn, exc, mention=()):
    try:
        fn()
    except exc as e:
        miss = [m for m in mention if m not in str(e)]
        if miss:
            FAILURES.append(f"{name}: message lacks {miss}")
        print(f"  [{'OK' if not miss else 'FAIL'}] {name}: {str(e)[:72]}")
        return
    except Exception as e:                                   # wrong type
        FAILURES.append(f"{name}: raised {type(e).__name__}, want {exc.__name__}")
        print(f"  [FAIL] {name} raised {type(e).__name__}")
        return
    FAILURES.append(f"{name}: did not raise")
    print(f"  [FAIL] {name} did not raise")


print("test_sources")
print(" A. every producer's spelling of one day maps to that day")
WANT = date(2016, 10, 31)
for label, raw in (("bulk CSV 'YYYY-MM-DD'", "2016-10-31"),
                   ("FEC API timestamp", "2016-10-31T00:00:00"),
                   ("timestamp with offset", "2016-10-31T00:00:00+00:00"),
                   ("space-separated", "2016-10-31 05:00:00"),
                   ("surrounding whitespace", " 2016-10-31 "),
                   ("datetime object", datetime(2016, 10, 31, 5, 0)),
                   ("date object", date(2016, 10, 31))):
    ok(label, _parse_record_date(raw), WANT)

print(" B. genuinely absent dates are None (dropped as dateless)")
ok("None", _parse_record_date(None), None)
ok("empty string", _parse_record_date(""), None)

print(" C. unreadable dates are None (dropped, and counted separately)")
for raw in ("not-a-date", "31/10/2016", "2016-13-45", "0000-00-00"):
    ok(f"{raw!r}", _parse_record_date(raw), None)

print(" D. a payload schema change raises, rather than aborting mid-comprehension")
for raw in (20161031, ["2016", "10", "31"], {"d": 1}):
    raises(f"{type(raw).__name__} date raises TypeError",
           lambda r=raw: _parse_record_date(r), TypeError,
           mention=["payload schema has changed"])

print(" E. the regression: the API timestamp is KEPT, not dropped")
old_style_fails = False
try:
    y, m, d = (int(x) for x in "2016-10-31T00:00:00".split("-"))
except ValueError:
    old_style_fails = True
ok("old parser rejected it", old_style_fails, True)
ok("new parser keeps it", _parse_record_date("2016-10-31T00:00:00") < CUTOFF, True)

print(" F. a wholesale format change stops the ETL (end-to-end)")
import json  # noqa: E402
import tempfile  # noqa: E402

import sources  # noqa: E402

TMP = Path(tempfile.mkdtemp())


def write_cache(name, dates):
    """A donor cache in the shape read() expects: {key: {'confirmed': [rec]}}."""
    path = TMP / f"{name}.json"
    path.write_text(json.dumps({
        f"DONOR {i}|GLEN COVE|11542": {
            "confirmed": [{"date": d, "amount": 25.0, "committee": "ACTBLUE"}]}
        for i, d in enumerate(dates)}))
    return path


def load_with(fec_dates, nyboe_dates=("2020-01-01",)):
    fec, nyboe = C_FEC, C_NYBOE
    sources.C.FEC_CACHE = write_cache("fec", fec_dates)
    sources.C.NYBOE_CACHE = write_cache("nyboe", nyboe_dates)
    try:
        return _load_donation_payloads(CUTOFF)
    finally:
        sources.C.FEC_CACHE, sources.C.NYBOE_CACHE = fec, nyboe


C_FEC, C_NYBOE = sources.C.FEC_CACHE, sources.C.NYBOE_CACHE

# Healthy: the two real-world formats mixed, all pre-cutoff, nothing tripped.
df = load_with(["2016-10-31", "2016-10-31T00:00:00", "2020-02-02"])
ok("mixed formats all kept", len(df[df["source"] == "fec"]), 3)

# Absent dates are dropped but never trip the guard, however many there are.
df = load_with(["2020-01-01"] + [None] * 40)
ok("40 absent dates drop without tripping", len(df[df["source"] == "fec"]), 1)

# Post-cutoff records are dropped, not counted as unparseable.
df = load_with(["2020-01-01", "2025-01-01"])
ok("post-cutoff dropped", len(df[df["source"] == "fec"]), 1)

# The reported scenario: the source's format changed wholesale.
raises("wholesale unparse raises",
       lambda: load_with(["31/10/2016"] * 40), ValueError,
       mention=["fec", "date format has changed"])

# Below threshold: 10 bad in 1000 is 1.0%, not >1% -> tolerated as noise.
raises_not = load_with(["2020-01-01"] * 990 + ["31/10/2016"] * 10)
ok("10 bad in 1,000 tolerated", len(raises_not[raises_not["source"] == "fec"]), 990)

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S):")
    for f in FAILURES:
        print(f"  - {f}")
    sys.exit(1)
print("ALL CHECKS PASSED")
