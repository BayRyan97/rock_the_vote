"""test_features_person.py — self-checks for the person-table definitions.

features_person.py's docstring claims ownership of "the household aggregates,
leave-self-out party shares, donation roll-ups, labels and ED context" — all
definitions, and until now none of them were pinned by anything.

The sharpest gap was num_reliable. Its constants were, per the comment,
"recovered by fitting against the CSV export's own columns (>=99.99% agreement
on 30k Nassau households)", and three other candidate rules "were wrong when
tested" — with the evidence living only in that prose. Section A below encodes
the discrimination, and section F re-runs the original fit against the real
export when it is present.

Run:  python model/test_features_person.py     (exit 0 = all checks pass)
"""
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "build"))

import config as C  # noqa: E402
from build import LOW_TIERS, parse_household  # noqa: E402
from features_person import (assemble, donation_features,  # noqa: E402
                             household_aggregates, leave_one_out_mean,
                             leave_self_out_shares)

FAILURES = []
E = C.TARGET_GENERAL_YEAR
REF_YEAR = int(C.REF_DATE[:4])


def ok(name, got, want):
    good = (list(got) == list(want)) if hasattr(got, "__iter__") and not isinstance(got, str) \
        else got == want
    if not good:
        FAILURES.append(f"{name}: got {got!r}, want {want!r}")
    print(f"  [{'OK' if good else 'FAIL'}] {name}" + ("" if good else f"  got={got!r}"))


def raises(name, fn, exc=ValueError, mention=()):
    try:
        fn()
    except exc as e:
        miss = [m for m in mention if m not in str(e)]
        if miss:
            FAILURES.append(f"{name}: message lacks {miss}")
        print(f"  [{'OK' if not miss else 'FAIL'}] {name}: {str(e)[:70]}")
        return
    except Exception as e:
        FAILURES.append(f"{name}: raised {type(e).__name__}")
        print(f"  [FAIL] {name}: raised {type(e).__name__}")
        return
    FAILURES.append(f"{name}: did not raise")
    print(f"  [FAIL] {name} did not raise")


def people(hh_uuids, tiers=None, parties=None, ages=None, donor_keys=None):
    n = len(hh_uuids)
    tiers = tiers or ["X4"] * n
    return pd.DataFrame({
        "person_uuid": [f"p{i}" for i in range(n)],
        "household_uuid": hh_uuids,
        "name": [f"N{i}" for i in range(n)],
        "age": ages or [40] * n,
        "party": parties or ["DEM"] * n,
        "tier_letter": [t[0] for t in tiers],
        "tier_count": [int(t[1:]) for t in tiers],
        "donor_key": donor_keys or [f"k{i}" for i in range(n)],
    })


print("test_features_person")

# ---------------------------------------------------------------- A
print(" A. household_aggregates — the reverse-engineered definitions")
# One household whose tiers separate the correct num_reliable rule from the
# three the comment says were tried and rejected.
TIERS = ["X5", "X3", "I4", "F1", "X0", "X2"]
agg = household_aggregates(people(["h"] * len(TIERS), tiers=TIERS,
                                  ages=[70, 60, 50, 40, 30, 20]))
counts = {
    "correct (letter X AND count>=4)": sum(t[0] == "X" and int(t[1:]) >= 4 for t in TIERS),
    "rejected: not in LOW_TIERS":      sum(t not in LOW_TIERS for t in TIERS),
    "rejected: letter X":              sum(t[0] == "X" for t in TIERS),
    "rejected: count >= 3":            sum(int(t[1:]) >= 3 for t in TIERS),
}
print(f"       fixture {TIERS} separates all four: {list(counts.values())}")
ok("num_reliable is X and count>=4", agg["num_reliable"].tolist(), [1])
for label, v in counts.items():
    if label.startswith("rejected"):
        ok(f"  not {label[10:]}", int(agg['num_reliable'].iloc[0]) != v, True)
ok("num_low_engagement counts LOW_TIERS", agg["num_low_engagement"].tolist(), [1])
ok("voters_at_address is the group size", agg["voters_at_address"].tolist(), [6])
ok("max_vote_count", agg["max_vote_count"].tolist(), [5])
ok("min_vote_count", agg["min_vote_count"].tolist(), [0])
ok("engagement_gap is max - min", agg["engagement_gap"].tolist(), [5])
ok("oldest_age", agg["oldest_age"].tolist(), [70])
ok("youngest_age", agg["youngest_age"].tolist(), [20])

two = household_aggregates(people(["a", "a", "b"], tiers=["X4", "F1", "X5"]))
ok("aggregates are per household", sorted(two["voters_at_address"].tolist()), [1, 2])

# ---------------------------------------------------------------- B
print(" B. leave-one-out: the singleton convention is load-bearing")
v = pd.Series([1.0, 2.0, 6.0, 9.0])
k = pd.Series(["a", "a", "a", "b"])
# a: excluding self -> (2+6)/2=4, (1+6)/2=3.5, (1+2)/2=1.5 ; b: singleton -> 0.0
ok("mean excludes the row itself", leave_one_out_mean(v, k).round(4).tolist(),
   [4.0, 3.5, 1.5, 0.0])
ok("singleton groups get 0.0, not NaN",
   bool(leave_one_out_mean(pd.Series([5.0]), pd.Series(["x"])).notna().all()), True)
ok("index is preserved",
   leave_one_out_mean(pd.Series([1.0, 2.0], index=[7, 9]),
                      pd.Series(["a", "a"], index=[7, 9])).index.tolist(), [7, 9])

print(" C. leave_self_out_shares folds fusion parties via C.PARTY_CLASS")
p = pd.DataFrame({"party": ["DEM", "WOR", "REP", "CON", "BLK"],
                  "hh": ["h"] * 5})
shares, size = leave_self_out_shares(p, "hh", "hh")
# For the DEM row: others are WOR(dem), REP, CON, BLK -> dem share 1/4
ok("WOR counts as dem", round(float(shares["hh_dem_share_excl"].iloc[0]), 4), 0.25)
ok("CON counts as rep", round(float(shares["hh_rep_share_excl"].iloc[0]), 4), 0.5)
ok("blk share excludes self", round(float(shares["hh_blk_share_excl"].iloc[4]), 4), 0.0)
ok("size is the group size", size.tolist(), [5] * 5)

# ---------------------------------------------------------------- D
print(" D. donation_features")
persons = pd.DataFrame({"donor_key": ["k1", "k1", "k2"]})   # two people share k1
don = pd.DataFrame([
    ("k1", "fec", "ACTBLUE", 25.0, pd.Timestamp("2024-01-01")),
    ("k1", "fec", "SOMEPAC", 10.0, pd.Timestamp("2020-01-01")),
    ("k1", "nyboe", "WINRED", 40.0, pd.Timestamp("2023-06-01")),
], columns=["donor_key", "source", "committee", "amount", "donation_date"])
f, d = donation_features(persons, don, date(2024, 11, 5))
ok("records attach to every person sharing a donor_key", f["fec_n"].tolist(), [2, 2, 0])
ok("per-source totals", f["fec_total"].round(2).tolist(), [35.0, 35.0, 0.0])
ok("conduit split: ACTBLUE -> dem", f["dem_conduit_total"].tolist(), [25.0, 25.0, 0.0])
ok("conduit split: WINRED -> rep", f["rep_conduit_total"].tolist(), [40.0, 40.0, 0.0])
ok("n_committees is distinct across sources", f["n_committees"].tolist(), [3, 3, 0])
ok("has_donation", f["has_donation"].tolist(), [1, 1, 0])
# recency measured back from the cutoff, most recent record wins
ok("fec recency from the cutoff", int(f["fec_recency_days"].iloc[0]),
   (pd.Timestamp("2024-11-05") - pd.Timestamp("2024-01-01")).days)
ok("non-donors get NaN recency", bool(np.isnan(f["nyboe_recency_days"].iloc[2])), True)
ok("donor edge rows fan out to both people", sorted(d["person_row"].tolist()),
   [0, 0, 0, 1, 1, 1])

print("    dtypes stay put whichever sources are present")
WANT = {"fec_n": "int16", "fec_total": "float32", "fec_recency_days": "float32",
        "nyboe_n": "int16", "nyboe_total": "float32", "nyboe_recency_days": "float32",
        "has_donation": "int8", "n_committees": "int16"}
empty = pd.DataFrame(columns=["donor_key", "source", "committee", "amount", "donation_date"])
for label, frame in (("all sources", don), ("fec only", don[don["source"] == "fec"]),
                     ("no donations", empty)):
    got, dd = donation_features(persons, frame, date(2024, 11, 5))
    bad = {c: str(got[c].dtype) for c, w in WANT.items() if str(got[c].dtype) != w}
    ok(f"  {label}", bad, {})
    ok(f"  {label}: donors.person_row is int32", str(dd["person_row"].dtype), "int32")

# ---------------------------------------------------------------- E
print(" E. assemble: labels, ED context, and the ballot alignment contract")


def frames(ages, parties, votes_year, hh="h", n_hh=1, orphan=False):
    n = len(ages)
    hhs = [f"{hh}{i % n_hh}" for i in range(n)]
    if orphan:
        hhs[-1] = "missing"
    H = pd.DataFrame({
        "household_uuid": [f"{hh}{i}" for i in range(n_hh)],
        "county": ["NASSAU"] * n_hh, "town": ["T"] * n_hh, "city": ["GC"] * n_hh,
        "zip_code": ["11542"] * n_hh, "address_number": ["1"] * n_hh,
        "street_name": ["A"] * n_hh,
        "election_district": np.arange(1, n_hh + 1, dtype=np.int16),
        "congressional_district": np.full(n_hh, 3, np.int16),
        "senate_district": np.full(n_hh, 7, np.int16),
        "assembly_district": np.full(n_hh, 12, np.int16),
        "legislative_district": [pd.NA] * n_hh,
        "lon": [-73.6] * n_hh, "lat": [40.8] * n_hh,
        "declared_voters": [hhs.count(f"{hh}{i}") for i in range(n_hh)],
    })
    P = people(hhs, parties=parties, ages=ages)
    rows = [(i, y) for i, y in enumerate(votes_year) if y]
    B = pd.DataFrame({
        "person_row": np.array([r[0] for r in rows], np.int32),
        "year": np.array([r[1] for r in rows], np.int16),
        "etype": pd.Categorical(["G"] * len(rows), categories=["G", "P"]),
        "method": pd.Categorical(["E"] * len(rows), categories=list("EVAFDMO")),
    })
    D = pd.DataFrame(columns=["donor_key", "source", "committee", "amount", "donation_date"])
    return H, P, B, D


# age is as of REF_YEAR; eligibility is age at the target general
young, old = 18 + (REF_YEAR - E) - 1, 18 + (REF_YEAR - E)
H, P, B, D = frames([old, young, old], ["DEM", "REP", "BLK"], [E, E, 0])
persons, donors, ballots = assemble(H, P, B, D, date(E, 11, 5))
ok("under-18-at-election is masked with -1", persons["y_turnout"].tolist(), [1, -1, 0])
ok("y_party folds DEM->0, REP->1, BLK->masked",
   persons["y_party"].tolist(), [0, 1, C.PARTY_MASKED])
ok("ed_key keeps district ints unformatted", persons["ed_key"].iloc[0], "NASSAU|12|1")
ok("person_id is a row ordinal", persons["person_id"].tolist(), [0, 1, 2])
ok("ballots pass through unchanged", len(ballots), 2)

# a ballot in a different year must not count as turnout in E
H, P, B, D = frames([old], ["DEM"], [E - 2])
ok("a pre-E general is not the E label",
   assemble(H, P, B, D, date(E, 11, 5))[0]["y_turnout"].tolist(), [0])

raises("orphaned household is refused",
       lambda: assemble(*frames([old, old], ["DEM", "REP"], [E, E], orphan=True),
                        date(E, 11, 5)),
       mention=["absent from the household table"])

# ---------------------------------------------------------------- F
print(" F. parity with the export's own columns (the original fit)")
csv = C.VOTER_SOURCES[0]
if not csv.exists():
    print(f"  [skip] {csv.name} not present (gitignored); synthetic checks above still hold")
else:
    raw = pd.read_csv(csv, nrows=5000)
    rows, hh_ids = [], []
    for i, rec in enumerate(raw.itertuples(index=False)):
        parsed = parse_household(rec.household_detail)
        for name, age, party, tier, _ in parsed:
            rows.append((str(i), tier[0], int(tier[1:]) if tier[1:].isdigit() else 0, age))
        if parsed:
            hh_ids.append(i)
    ppl = pd.DataFrame(rows, columns=["household_uuid", "tier_letter", "tier_count", "age"])
    got = household_aggregates(ppl).set_index("household_uuid")
    exp = raw.loc[hh_ids].copy()
    exp.index = [str(i) for i in hh_ids]
    print(f"       {len(got):,} households parsed from {csv.name}")
    for col in ("voters_at_address", "max_vote_count", "min_vote_count",
                "engagement_gap", "oldest_age", "youngest_age",
                "num_reliable", "num_low_engagement"):
        a = got[col].reindex(exp.index).to_numpy(float)
        b = pd.to_numeric(exp[col], errors="coerce").to_numpy(float)
        m = ~np.isnan(b)
        rate = float((a[m] == b[m]).mean()) if m.any() else float("nan")
        good = rate >= 0.999
        if not good:
            FAILURES.append(f"{col}: only {rate:.4%} agreement with the export")
        print(f"  [{'OK' if good else 'FAIL'}] {col:20s} {rate:.4%} agreement "
              f"({int(m.sum()):,} households compared)")

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S):")
    for f in FAILURES:
        print(f"  - {f}")
    sys.exit(1)
print("ALL CHECKS PASSED")
