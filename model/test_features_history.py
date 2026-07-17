"""test_features_history.py — self-checks for the as-of feature logic.

Four synthetic voters with hand-computed expectations exercise the rules the
pipeline's correctness hangs on: the year-E general is EXCLUDED from features
(even its vote method), the year-E primary is INCLUDED, eligibility
denominators de-age per year, streaks/recency/sentinels, method shares, and
leave-self-out household/ED aggregates. Also pins election_day().

Run:  python model/test_features_history.py     (exit 0 = all checks pass)
"""
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from etl import election_day                     # noqa: E402
from features_history import REF_YEAR, SENTINEL_YEARS, build_features  # noqa: E402

E = 2024
FAILURES = []


def check(name, actual, expected):
    ok = np.isclose(float(actual), float(expected), atol=1e-5)
    if not ok:
        FAILURES.append(f"{name}: got {actual}, expected {expected}")
    print(f"  [{'OK' if ok else 'FAIL'}] {name} = {actual}"
          + ("" if ok else f"  (expected {expected})"))


def make_elections(rows):
    return pd.DataFrame({
        "person_row": np.array([r[0] for r in rows], np.int32),
        "year": np.array([r[1] for r in rows], np.int16),
        "etype": pd.Categorical([r[2] for r in rows], categories=["G", "P"]),
        "method": pd.Categorical([r[3] for r in rows], categories=list("EVAFDMO")),
    })


persons = pd.DataFrame({
    "age": [64, 20, 40, 50],
    "household_row": [0, 0, 1, 1],
    "ed_key": ["A", "A", "A", "A"],
})
rows = []
# v0: generals 2016-2023 poll site, 2024 primary, 2024 general EARLY (post-
#     cutoff: must not appear in any feature), 2025 general (post-cutoff)
rows += [(0, y, "G", "E") for y in range(2016, 2024)]
rows += [(0, 2024, "P", "E"), (0, 2024, "G", "V"), (0, 2025, "G", "E")]
# v1: age 20 (18 first in 2024): only ballot is the 2024 general
rows += [(1, 2024, "G", "E")]
# v2: sparse lapsed voter: generals 2018 absentee + 2020 mail, 2022 primary
rows += [(2, 2018, "G", "A"), (2, 2020, "G", "M"), (2, 2022, "P", "E")]
# v3: no ballots at all
elections = make_elections(rows)
out = build_features(persons, elections, E)

print(f"REF_YEAR = {REF_YEAR} (expected 2026 for these expectations)")
assert REF_YEAR == 2026, "expectations below assume a 2026 export"

print("v0 (age 64, perfect 2016-2023 record; 2024-general early vote is post-cutoff):")
check("v0 n_generals", out.loc[0, "hist_n_generals"], 8)
check("v0 n_primaries", out.loc[0, "hist_n_primaries"], 1)
check("v0 general_rate_5", out.loc[0, "hist_general_rate_5"], 1.0)
check("v0 general_rate_8", out.loc[0, "hist_general_rate_8"], 1.0)
check("v0 general_rate_12 (8 data years / 12 eligible)", out.loc[0, "hist_general_rate_12"], 8 / 12)
check("v0 pres_rate", out.loc[0, "hist_pres_rate"], 1.0)
check("v0 midterm_rate", out.loc[0, "hist_midterm_rate"], 1.0)
check("v0 oddyear_rate", out.loc[0, "hist_oddyear_rate"], 1.0)
check("v0 years_since_last_vote (2024 primary counts)", out.loc[0, "hist_years_since_last_vote"], 0)
check("v0 years_since_last_general", out.loc[0, "hist_years_since_last_general"], 1)
check("v0 years_since_first_seen", out.loc[0, "hist_years_since_first_seen"], 8)
check("v0 streak_current", out.loc[0, "hist_streak_current"], 8)
check("v0 streak_longest", out.loc[0, "hist_streak_longest"], 8)
check("v0 trend_3v5", out.loc[0, "hist_trend_3v5"], 0.0)
check("v0 voted_primary_cycle", out.loc[0, "hist_voted_primary_cycle"], 1)
check("v0 share_pollsite (9 pre-E ballots, all poll site)", out.loc[0, "hist_share_pollsite"], 1.0)
check("v0 share_early EXCLUDES the 2024-general early vote", out.loc[0, "hist_share_early"], 0.0)
check("v0 label voted 2024 general", out.loc[0, f"y_voted_general_{E}"], 1)
check("v0 age_at_target", out.loc[0, "hist_age_at_target"], 62)

print("v1 (age 20: first eligible general IS the target; first-ever ballot = label):")
check("v1 eligible_generals_8 (18+ only from 2024)", out.loc[1, "hist_eligible_generals_8"], 0)
check("v1 general_rate_8 (0/0 -> 0)", out.loc[1, "hist_general_rate_8"], 0.0)
check("v1 never_voted (no pre-E ballots)", out.loc[1, "hist_never_voted"], 1)
check("v1 years_since_last_vote sentinel", out.loc[1, "hist_years_since_last_vote"], SENTINEL_YEARS)
check("v1 label voted 2024 general", out.loc[1, f"y_voted_general_{E}"], 1)

print("v2 (lapsed: generals 2018+2020, primary 2022):")
check("v2 n_generals", out.loc[2, "hist_n_generals"], 2)
check("v2 general_rate_5", out.loc[2, "hist_general_rate_5"], 0.2)
check("v2 general_rate_8", out.loc[2, "hist_general_rate_8"], 0.25)
check("v2 years_since_last_vote (2022 primary)", out.loc[2, "hist_years_since_last_vote"], 2)
check("v2 years_since_last_general", out.loc[2, "hist_years_since_last_general"], 4)
check("v2 voted_g4 (2020)", out.loc[2, "hist_voted_g4"], 1)
check("v2 voted_g1 (2023)", out.loc[2, "hist_voted_g1"], 0)
check("v2 streak_current", out.loc[2, "hist_streak_current"], 0)
check("v2 streak_longest", out.loc[2, "hist_streak_longest"], 1)
check("v2 trend_3v5 (0/3 recent - 2/5 older)", out.loc[2, "hist_trend_3v5"], -0.4)
check("v2 pres_rate (2020 of 2016+2020)", out.loc[2, "hist_pres_rate"], 0.5)
check("v2 share_absentee (A+M of 3 ballots)", out.loc[2, "hist_share_absentee"], 2 / 3)
check("v2 label", out.loc[2, f"y_voted_general_{E}"], 0)

print("v3 (no ballots at all):")
check("v3 never_voted", out.loc[3, "hist_never_voted"], 1)
check("v3 years_since_first_seen sentinel", out.loc[3, "hist_years_since_first_seen"], SENTINEL_YEARS)
check("v3 n_votes", out.loc[3, "hist_n_votes"], 0)
check("v3 label", out.loc[3, f"y_voted_general_{E}"], 0)

print("leave-self-out aggregates:")
check("v0 hh_excl = v1 rate8", out.loc[0, "hist_hh_general_rate_8_excl"], 0.0)
check("v1 hh_excl = v0 rate8", out.loc[1, "hist_hh_general_rate_8_excl"], 1.0)
check("v3 hh_excl = v2 rate8", out.loc[3, "hist_hh_general_rate_8_excl"], 0.25)
check("v3 ed_excl = mean(v0,v1,v2)", out.loc[3, "hist_ed_general_rate_8_excl"], (1.0 + 0.0 + 0.25) / 3)
check("v0 ed_excl = mean(v1,v2,v3)", out.loc[0, "hist_ed_general_rate_8_excl"], 0.25 / 3)

print("election_day():")
for year, expected in ((2020, date(2020, 11, 3)), (2022, date(2022, 11, 8)),
                       (2024, date(2024, 11, 5)), (2026, date(2026, 11, 3))):
    got = election_day(year)
    ok = got == expected
    if not ok:
        FAILURES.append(f"election_day({year}): got {got}, expected {expected}")
    print(f"  [{'OK' if ok else 'FAIL'}] election_day({year}) = {got}")

if FAILURES:
    print(f"\n{len(FAILURES)} FAILURES:")
    for f in FAILURES:
        print(" ", f)
    sys.exit(1)
print("\nALL CHECKS PASSED")
