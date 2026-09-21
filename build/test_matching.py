#!/usr/bin/env python3
"""test_matching.py — self-checks for build/matching.py.

Plain python, no pytest, no network, no DB, no voter file.
    python build/test_matching.py     # exit 0 = pass
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from matching import (  # noqa: E402
    normalize_city, split_zip, name_tokens, blocking_keys, names_compatible,
    score_match, classify, cache_key, CONFIRM_THRESHOLD,
)

FAILURES = []


def ok(name, got, want):
    if got == want:
        print(f"    [OK]   {name}")
    else:
        print(f"    [FAIL] {name}: got {got!r}, want {want!r}")
        FAILURES.append(name)


def cand(tokens, city, zipc, **extra):
    return {"name_tokens": frozenset(tokens), "city": city, "zip": zipc, **extra}


print("\n A. city normalization — rule 1, the biggest measured recall win")
print("    FEC carries 1,647 rows for EAST ISLIP against 8 for E ISLIP; under the")
print("    old exact-string test every voter in 'E ISLIP' was unmatchable.")
ok("E ISLIP -> EAST ISLIP", normalize_city("E Islip"), "EAST ISLIP")
ok("N BABYLON -> NORTH BABYLON", normalize_city("N Babylon"), "NORTH BABYLON")
ok("ST JAMES -> SAINT JAMES", normalize_city("St James"), "SAINT JAMES")
ok("MT SINAI -> MOUNT SINAI", normalize_city("Mt. Sinai"), "MOUNT SINAI")
ok("already spelled out is unchanged", normalize_city("EAST MEADOW"), "EAST MEADOW")
ok("trailing VILLAGE dropped", normalize_city("Hempstead Village"), "HEMPSTEAD")
ok("a lone directional is not mangled", normalize_city("NORTH"), "NORTH")
ok("empty stays empty", normalize_city(""), "")

print("\n B. zip handling — rule 3")
ok("plain zip5", split_zip("11756"), ("11756", ""))
ok("zip+4 is retained, not truncated", split_zip("117561234"), ("11756", "1234"))
ok("hyphenated zip+4", split_zip("11756-1234"), ("11756", "1234"))
ok("junk yields empty", split_zip(""), ("", ""))

print("\n C. name tokens — rules 5 and 6")
ok("suffix stripped", name_tokens("JOHN SMITH JR"), ["JOHN", "SMITH"])
ok("nickname resolved", name_tokens("Bob Smith"), ["ROBERT", "SMITH"])
ok("middle initial kept as a token", name_tokens("JOHN M SMITH"), ["JOHN", "M", "SMITH"])

print("\n C2. 'LAST, FIRST' names — NYC CFB stores one combined NAME field")
print("     name_tokens folds nicknames on the FIRST token, so a comma-ordered")
print("     name would run the SURNAME through the nickname table.")
from matching import swap_comma_name
ok("comma form is reordered", swap_comma_name("SMITH, JOHN M"), "JOHN M SMITH")
ok("no comma is left alone", swap_comma_name("JOHN SMITH"), "JOHN SMITH")
ok("empty stays empty", swap_comma_name(""), "")
ok("surname WILL is NOT folded to WILLIAM",
   name_tokens(swap_comma_name("Will, Robert")), ["ROBERT", "WILL"])
print("    ^ without the swap this returns ['WILLIAM','ROBERT'] — a surname")
print("      silently rewritten into a different person's first name.")
ok("a real first-name nickname still folds",
   name_tokens(swap_comma_name("Smith, Bob")), ["ROBERT", "SMITH"])

print("\n D. blocking — a suffix must not become the lookup key")
ok("suffix does not swallow the surname", "SMITH" in blocking_keys("JOHN SMITH JR"), True)
ok("plain surname present", "SMITH" in blocking_keys("JOHN SMITH"), True)

print("\n E. names_compatible — middle tokens optional, first+last required")
print("    74,086 voters (4.0%) carry a middle token; under strict subset every")
print("    one of them failed against a filer who omitted it.")
ok("voter middle initial, filer without it",
   names_compatible("JOHN M SMITH", frozenset({"JOHN", "SMITH"})), True)
ok("filer has extra middle, voter without it",
   names_compatible("JOHN SMITH", frozenset({"JOHN", "SMITH", "MICHAEL"})), True)
ok("voter initial matches filer's full middle",
   names_compatible("JOHN M SMITH", frozenset({"JOHN", "MICHAEL", "SMITH"})), True)
ok("different first name is NOT compatible",
   names_compatible("JANE SMITH", frozenset({"JOHN", "SMITH"})), False)
ok("surname alone is not enough",
   names_compatible("SMITH", frozenset({"JOHN", "SMITH"})), False)
ok("nickname bridges to formal name",
   names_compatible("BOB SMITH", frozenset({"ROBERT", "SMITH"})), True)

print("\n F. scoring — zip5 carries confirmation, city corroborates (rule 2)")
T = frozenset({"JOHN", "SMITH"})
s_both, _ = score_match("JOHN SMITH", "E ISLIP", "11730", T, "EAST ISLIP", "11730")
ok("abbreviated vs spelled-out city now CONFIRMS", s_both >= CONFIRM_THRESHOLD, True)
s_nocity, _ = score_match("JOHN SMITH", "", "11730", T, "EAST ISLIP", "11730")
ok("zip5 alone still confirms when city is absent", s_nocity >= CONFIRM_THRESHOLD, True)
s_diff, _ = score_match("JOHN SMITH", "HEMPSTEAD", "11730", T, "EAST ISLIP", "11730")
ok("a positively DIFFERENT city drops below confirm", s_diff < CONFIRM_THRESHOLD, True)
s_nozip, _ = score_match("JOHN SMITH", "EAST ISLIP", "11550", T, "EAST ISLIP", "11730")
ok("city agreement alone never confirms", s_nozip < CONFIRM_THRESHOLD, True)
s_p4, _ = score_match("JOHN SMITH", "EAST ISLIP", "117301234", T, "EAST ISLIP", "117301234")
ok("matching zip+4 scores above zip5 alone", s_p4 > s_both, True)
s_p4x, _ = score_match("JOHN SMITH", "EAST ISLIP", "117301234", T, "EAST ISLIP", "117309999")
ok("differing zip+4 costs confidence", s_p4x < s_p4, True)

print("\n G. classify — the possible list is RANKED before it is capped (rule 4)")
cands = [cand({"JOHN", "SMITH"}, "HEMPSTEAD", "11550", tag=f"weak{i}") for i in range(12)]
cands.append(cand({"JOHN", "SMITH"}, "EAST ISLIP", "11730", tag="strong"))
conf, poss = classify(cands, "JOHN SMITH", "EAST ISLIP", "11730", possible_cap=3)
ok("the one address-agreeing candidate confirms", len(conf), 1)
ok("  and it is the right one", conf[0]["tag"], "strong")
ok("possible list respects the cap", len(poss), 3)
ok("every candidate carries a score", all("match_score" in r for r in poss), True)
ok("possible list is sorted best-first",
   poss == sorted(poss, key=lambda r: -r["match_score"]), True)
ok("name_tokens is not leaked into the record", "name_tokens" in poss[0], False)

print("\n G2. the fast path must agree with the reference implementation")
print("     score_probe/classify_rows exist only for speed — the first cut ran")
print("     six hours on the real file. They must produce the SAME numbers as")
print("     score_match/classify or the optimisation has changed the answer.")
import random as _rnd
from matching import VoterProbe, score_probe, classify_rows, normalize_city, split_zip
_rnd.seed(7)
_names = ["JOHN SMITH", "JOHN M SMITH", "BOB SMITH", "JANE SMITH", "JOHN SMITH JR"]
_filers = [frozenset({"JOHN", "SMITH"}), frozenset({"JOHN", "MICHAEL", "SMITH"}),
           frozenset({"ROBERT", "SMITH"}), frozenset({"JANE", "SMITH"})]
_cities = ["E ISLIP", "EAST ISLIP", "HEMPSTEAD", ""]
_zips = ["11730", "11550", "117301234", ""]
_mismatch = 0
for vn in _names:
    for ft in _filers:
        for vc in _cities:
            for fc in _cities:
                for vz in _zips:
                    for fz in _zips:
                        a = score_match(vn, vc, vz, ft, fc, fz)
                        b = score_probe(VoterProbe(vn, vc, vz), ft,
                                        normalize_city(fc), *split_zip(fz))
                        if a != b:
                            _mismatch += 1
ok(f"score_probe == score_match across {len(_names)*len(_filers)*len(_cities)**2*len(_zips)**2:,} combinations",
   _mismatch, 0)

_rows = [("|".join(sorted({"JOHN", "SMITH"})), normalize_city("EAST ISLIP"), "11730", "", "2024-01-01", 100.0, "A"),
         ("|".join(sorted({"JOHN", "SMITH"})), normalize_city("HEMPSTEAD"), "11550", "", "2024-01-01", 50.0, "B"),
         ("|".join(sorted({"JANE", "SMITH"})), normalize_city("EAST ISLIP"), "11730", "", "2024-01-01", 75.0, "C")]
_c1, _p1 = classify_rows(_rows, "JOHN SMITH", "E ISLIP", "11730",
                         payload=lambda r: {"committee": r[6]})
_c2, _p2 = classify([cand(r[0].split("|"), "EAST ISLIP" if r[6] != "B" else "HEMPSTEAD",
                          r[2], committee=r[6]) for r in _rows],
                    "JOHN SMITH", "E ISLIP", "11730")
ok("classify_rows confirms the same set",
   [r["committee"] for r in _c1], [r["committee"] for r in _c2])
ok("classify_rows scores the same",
   [r["match_score"] for r in _c1], [r["match_score"] for r in _c2])
ok("wrong first name is rejected by the fast pre-filter",
   any(r["committee"] == "C" for r in _c1 + _p1), False)

print("\n G3. name ambiguity — demoting the cases donor_key silently merges")
print("     Measured on the voter file: 97.4% of people are the ONLY holder of")
print("     their first+last inside their own ZIP. So rarity cannot spread the")
print("     histogram — it exists for the 45,781 keys that ARE shared, because")
print("     those are exactly the people donor_key = NAME|CITY|ZIP5 merges into")
print("     one identity, and the addresses agreeing is what makes it dangerous.")
from matching import build_name_frequency, ambiguity_penalty
T2 = frozenset({"JOHN", "SMITH"})
ok("unique name keeps the full address score",
   score_match("JOHN SMITH", "E ISLIP", "11730", T2, "EAST ISLIP", "11730")[0], 90)
for shared, want in ((3, 75), (6, 65), (20, 55)):
    s, r = score_match("JOHN SMITH", "E ISLIP", "11730", T2, "EAST ISLIP", "11730",
                       {("JOHN", "SMITH", "11730"): shared})
    ok(f"shared by {shared} demotes to {want}", s, want)
    ok(f"  and says so in the reasons", f"shared-name-{shared}" in r, True)
ok("a 6-way shared name drops BELOW confirm",
   score_match("JOHN SMITH", "E ISLIP", "11730", T2, "EAST ISLIP", "11730",
               {("JOHN", "SMITH", "11730"): 6})[0] < CONFIRM_THRESHOLD, True)
print("    ^ 3-way stays confirmed on purpose: two or three people sharing a")
print("      name in one ZIP is common and the address still agrees; six is")
print("      where the key stops identifying a person.")
ok("ambiguity is scoped to the voter's OWN zip",
   score_match("JOHN SMITH", "E ISLIP", "11730", T2, "EAST ISLIP", "11730",
               {("JOHN", "SMITH", "11550"): 20})[0], 90)

print("\n     build_name_frequency must count BEFORE dedup. The cache key IS")
print("     NAME|CITY|ZIP5, so iterating voters deduplicated destroys exactly")
print("     the collisions being counted — that bug found 4,513 shared pairs")
print("     against a true 45,781, leaving the penalty nearly inert.")
freq = build_name_frequency([("JOHN SMITH", "11730"), ("JOHN SMITH", "11730"),
                             ("JANE DOE", "11730"), ("BOB SMITH", "11550")])
ok("a shared pair is recorded", freq.get(("JOHN", "SMITH", "11730")), 2)
ok("a unique pair is omitted", ("JANE", "DOE", "11730") in freq, False)
ok("nicknames fold before counting", ("ROBERT", "SMITH", "11550") in freq, False)
ok("zip+4 input keys on zip5",
   build_name_frequency([("A B", "11730-1234"), ("A B", "11730")]).get(("A", "B", "11730")), 2)
ok("no map behaves exactly as before",
   score_match("JOHN SMITH", "E ISLIP", "11730", T2, "EAST ISLIP", "11730", None)[0], 90)
ok("penalty tiers are monotonic",
   [ambiguity_penalty(n) for n in (1, 2, 5, 50)], [0, -15, -25, -35])

print("\n H. cache_key — rule 7, the double-counting bug")
print("    fetch_fec.py did not uppercase city while fetch_fec_bulk.py did, and")
print("    both wrote the same cache file. One person, two keys, doubled dollars.")
ok("lowercase city yields the same key",
   cache_key("JOHN SMITH", "east islip", "11730"),
   cache_key("JOHN SMITH", "EAST ISLIP", "11730"))
ok("zip+4 input still keys on zip5",
   cache_key("JOHN SMITH", "EAST ISLIP", "11730-1234"), "JOHN SMITH|EAST ISLIP|11730")
ok("key keeps the RAW city, not the normalized one",
   cache_key("JOHN SMITH", "E ISLIP", "11730"), "JOHN SMITH|E ISLIP|11730")
print("    ^ deliberate: the key must stay byte-compatible with the")
print("      people.donor_key generated column, or every existing row orphans.")

print("\n I. the suffix bug we deliberately did NOT chase")
print("    'SMITH JR' blocking on 'JR' is real, but affects 29 of 1,854,934")
print("    voters (0.00%). blocking_keys covers it anyway, at no cost.")
ok("suffixed voter still reaches the SMITH bucket",
   "SMITH" in blocking_keys("JOHN SMITH JR"), True)

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILED:")
    for f in FAILURES:
        print(f"  - {f}")
    sys.exit(1)
print("ALL CHECKS PASSED")
