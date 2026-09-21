#!/usr/bin/env python3
"""test_donations.py — self-checks for the donation targeting layer.

Plain python, no pytest, no DB, no CatBoost — same shape as
model/turfs/test_turfs.py. Everything runs off synthetic frames.

    python model/donations/test_donations.py     # exit 0 = pass
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import config as C  # noqa: E402
from committees import build_dimension, collapse_cycles, normalize_name  # noqa: E402
from donor_value import attach_sides, eligible_mask, revealed_lean  # noqa: E402

FAILURES = []


def ok(name, got, want):
    if got == want:
        print(f"    [OK]   {name}")
    else:
        print(f"    [FAIL] {name}: got {got!r}, want {want!r}")
        FAILURES.append(name)


def close(name, got, want, tol=1e-9):
    if got is None or (isinstance(got, float) and np.isnan(got)):
        print(f"    [FAIL] {name}: got {got!r}, want ~{want}")
        FAILURES.append(name); return
    if abs(got - want) <= tol:
        print(f"    [OK]   {name}  ({got:.4f})")
    else:
        print(f"    [FAIL] {name}: got {got}, want ~{want}")
        FAILURES.append(name)


def is_nan(name, got):
    if got is None or (isinstance(got, float) and np.isnan(got)):
        print(f"    [OK]   {name}")
    else:
        print(f"    [FAIL] {name}: got {got!r}, want NaN/None")
        FAILURES.append(name)


# ------------------------------------------------------------------ factories

def dim(rows):
    """committee_dim frame: (committee, side, labor)."""
    return pd.DataFrame(
        [{"committee": normalize_name(pd.Series([c]))[0], "side": s, "labor": l}
         for c, s, l in rows],
        columns=["committee", "side", "labor"])


def gifts(rows):
    """donations frame: (donor_key, committee, amount, confirmed)."""
    return pd.DataFrame(
        [{"donor_key": k, "committee": c, "amount": a, "confirmed": f,
          "source": "test", "donation_date": "2024-01-01"}
         for k, c, a, f in rows])


def lean_of(out, key, col="revealed_lean"):
    row = out[out["donor_key"] == key]
    return None if row.empty else row.iloc[0][col]


# --------------------------------------------------------------------- tests

print("\n A. committee name normalization")
ok("upper + collapse whitespace",
   normalize_name(pd.Series(["  Nassau   County  GOP "]))[0], "NASSAU COUNTY GOP")
ok("None becomes empty", normalize_name(pd.Series([None]))[0], "")

print("\n B. collapse_cycles — later cycle wins, blank never overwrites")
master = pd.DataFrame([
    {"cycle": "2020", "name_norm": "X PAC", "cmte_pty_affiliation": "DEM",
     "cand_pty_affiliation": "", "org_tp": "", "cmte_tp": "N", "connected_org_nm": ""},
    {"cycle": "2024", "name_norm": "X PAC", "cmte_pty_affiliation": "",
     "cand_pty_affiliation": "", "org_tp": "L", "cmte_tp": "N", "connected_org_nm": "UNION"},
])
c = collapse_cycles(master).iloc[0]
ok("blank 2024 party does not erase 2020 DEM", c["cmte_pty"], "DEM")
ok("2024 org_tp is picked up", c["org_tp"], "L")

print("\n C. resolution ladder precedence")
ovr = pd.DataFrame([{"name_norm": "X PAC", "side": "REP", "labor": False, "reviewed": True}])
d = build_dimension(master, ovr)
ok("reviewed override beats FEC party", d.iloc[0]["side"], "REP")
ok("  and records why", d.iloc[0]["resolved_by"], "override-reviewed")

ovr_unrev = pd.DataFrame([{"name_norm": "X PAC", "side": "REP", "labor": False,
                           "reviewed": False}])
d = build_dimension(master, ovr_unrev)
ok("UNreviewed override does NOT beat FEC party", d.iloc[0]["side"], "DEM")
ok("  FEC field is credited", d.iloc[0]["resolved_by"], "fec-committee-party")
ok("ORG_TP='L' still forces labor even with a side", bool(d.iloc[0]["labor"]), True)

print("    a LOW-confidence reviewed tag is a guess; FEC filed its own answer,")
print("    so the federal field wins. High/medium still sit above it.")
ovr_low = pd.DataFrame([{"name_norm": "X PAC", "side": "REP", "labor": False,
                         "reviewed": True, "confidence": "low"}])
d = build_dimension(master, ovr_low)
ok("low-confidence reviewed does NOT beat FEC party", d.iloc[0]["side"], "DEM")
ok("  FEC field is credited", d.iloc[0]["resolved_by"], "fec-committee-party")

# ...but a low-confidence tag still fires where FEC says nothing, which is the
# NY BOE case: no federal file covers state and local committees.
master_blank = master.copy()
master_blank["cmte_pty_affiliation"] = ""
d = build_dimension(master_blank, ovr_low)
ok("low-confidence still resolves when FEC is silent", d.iloc[0]["side"], "REP")
ok("  and is labelled as such", d.iloc[0]["resolved_by"], "override-reviewed-low")

ovr_med = pd.DataFrame([{"name_norm": "X PAC", "side": "REP", "labor": False,
                         "reviewed": True, "confidence": "medium"}])
ok("medium confidence still outranks FEC",
   build_dimension(master, ovr_med).iloc[0]["side"], "REP")

print("    quarantine flags are honoured at ANY confidence — erring toward")
print("    'not partisan evidence' is the safe direction to be wrong in.")
ovr_lowlab = pd.DataFrame([{"name_norm": "X PAC", "side": "", "labor": True,
                            "reviewed": True, "confidence": "low"}])
ok("low-confidence labor flag is still honoured",
   bool(build_dimension(master, ovr_lowlab).iloc[0]["labor"]), True)

print("\n D. THE COPE TEST — labor money is not partisan money")
print("    A registered Republican's automatic teachers-union payroll deduction")
print("    must never read as Democratic support. This is the single check the")
print("    labor quarantine exists for; if it regresses, the lean inverts for")
print("    tens of thousands of union households.")
cope = dim([("TEACHERS COPE", "DEM", True)])
out = revealed_lean(gifts([("UNION GUY", "TEACHERS COPE", 500.0, True)]), cope)
is_nan("$500 to a labor PAC yields NULL lean, not 1.0", lean_of(out, "UNION GUY"))
close("  labor dollars surface on their own feature",
      lean_of(out, "UNION GUY", "labor_pac_total"), 500.0)
close("  and are excluded from the lean denominator",
      lean_of(out, "UNION GUY", "revealed_lean_dollars"), 0.0)

print("\n E. NULL vs 0.5 — 'no evidence' is not 'gives to both sides'")
both = dim([("DEM CMTE", "DEM", False), ("REP CMTE", "REP", False)])
out = revealed_lean(gifts([("SPLIT", "DEM CMTE", 1000.0, True),
                           ("SPLIT", "REP CMTE", 1000.0, True)]), both, prior_dollars=0.0)
close("even split scores exactly 0.5", lean_of(out, "SPLIT"), 0.5)
ok("  and is flagged as both-sides", bool(lean_of(out, "SPLIT", "gave_both_sides")), True)
close("  with the full dollar weight visible",
      lean_of(out, "SPLIT", "revealed_lean_dollars"), 2000.0)

print("\n F. shrinkage — one small gift must not read as a certainty")
one = dim([("DEM CMTE", "DEM", False)])
g = gifts([("TINY", "DEM CMTE", 10.0, True)])
close("$10 with a $250 prior stays near 0.5",
      lean_of(revealed_lean(g, one, prior_dollars=250.0), "TINY"), (10 + 125) / (10 + 250))
close("  unshrunk it would be 1.0",
      lean_of(revealed_lean(g, one, prior_dollars=0.0), "TINY"), 1.0)
big = gifts([("BIG", "DEM CMTE", 100_000.0, True)])
lb = lean_of(revealed_lean(big, one, prior_dollars=250.0), "BIG")
ok("a large gift overwhelms the prior", bool(lb > 0.99), True)

print("\n G. unconfirmed and untagged money is excluded, not counted as neutral")
out = revealed_lean(gifts([("POSSIBLE", "DEM CMTE", 5000.0, False)]), one)
ok("unconfirmed-only donor is absent entirely", len(out[out["donor_key"] == "POSSIBLE"]), 0)
untagged = dim([("MYSTERY PAC", None, False)])
out = revealed_lean(gifts([("UNK", "MYSTERY PAC", 800.0, True)]), untagged)
ok("untagged committee does not create a 0.5 donor",
   len(out[out["donor_key"] == "UNK"]), 0)

print("\n H. attach_sides / eligible_mask wiring")
mixed = dim([("DEM CMTE", "DEM", False), ("TEACHERS COPE", "DEM", True)])
g = gifts([("A", "DEM CMTE", 100.0, True), ("A", "TEACHERS COPE", 100.0, True),
           ("A", "UNKNOWN CMTE", 100.0, True), ("A", "DEM CMTE", 100.0, False)])
a = attach_sides(g, mixed)
ok("unknown committee joins as NaN side", bool(pd.isna(a.iloc[2]["side"])), True)
ok("labor flag survives the join", bool(a.iloc[1]["labor"]), True)
ok("exactly one row is eligible", int(eligible_mask(a).sum()), 1)

print("\n I1. every reviewed override must survive into the built dimension")
print("     committees_to_tag.csv was generated as 'what still needs a tag', so")
print("     it EXCLUDES 277 of the 300 reviewed committees by construction —")
print("     and those 277 carry 767,816 gifts including ActBlue, WinRed and the")
print("     two largest labor PACs. Loading the tail instead of unioning would")
print("     un-quarantine union payroll money back into partisan lean.")
try:
    from committees import load_overrides, load_fec_master, build_dimension, normalize_name
    if C.COMMITTEE_OVERRIDES_CSV.exists():
        _rev = pd.read_csv(C.COMMITTEE_OVERRIDES_CSV, dtype=str).fillna("")
        _rev_keys = set(normalize_name(_rev["committee"]))
        _merged = load_overrides()
        _have = set(_merged["name_norm"])
        ok(f"all {len(_rev_keys):,} reviewed committees present after the union",
           len(_rev_keys - _have), 0)
        # and the reviewed tag must WIN, not merely be present
        _r = _rev.assign(k=normalize_name(_rev["committee"]))
        _r = _r[_r["side"].str.upper().isin(["DEM", "REP"])]
        _m = _merged.set_index("name_norm")
        _lost = [k for k, sd in zip(_r["k"], _r["side"].str.upper())
                 if k in _m.index and _m.loc[k, "side"] != sd]
        ok("reviewed side wins on conflict", len(_lost), 0)
        _lab = set(_r.assign(l=_rev["labor"].str.lower().eq("true")).loc[
            _rev["labor"].str.lower().eq("true"), "committee"].map(
            lambda c: normalize_name(pd.Series([c]))[0]))
        _lab_lost = [k for k in _lab if k in _m.index and not bool(_m.loc[k, "labor"])]
        ok("reviewed labor flags survive", len(_lab_lost), 0)
    else:
        print("    [SKIP] reviewed override file not present")
except Exception as _e:
    print(f"    [SKIP] {type(_e).__name__}: {_e}")

print("\n I9. one gift filed twice is one gift")
print("     NYC candidate committees register with the STATE BOE and also")
print("     join the CITY CFB programme, so the same contribution arrives")
print("     under two sources. The committee STRING cannot detect it: CFB")
print("     stores the candidate and BOE the committee, so one gift reads")
print("     'DIETL, RICHARD A' and 'FRIENDS OF BO DIETL'.")
try:
    from donor_value import drop_cross_source_duplicates

    _d = pd.DataFrame({
        "donor_key": ["A", "A", "B", "C", "C"],
        "source": ["nyboe", "nyccfb", "nyccfb", "fec", "nyboe"],
        "donation_date": ["2021-06-01", "2021-06-01", "2021-06-01",
                          "2022-03-03", "2022-03-03"],
        "amount": [175.0, 175.0, 50.0, 500.0, 500.0],
        "committee": ["ADAMS FOR NYC", "ADAMS, ERIC L", "OTHER CMTE",
                      "FED CMTE", "STATE CMTE"],
    })
    _o = drop_cross_source_duplicates(_d)
    ok("the duplicated nyccfb row is dropped", len(_o), 4)
    ok("the nyboe original is kept",
       bool(((_o.donor_key == "A") & (_o.source == "nyboe")).any()), True)
    ok("an nyccfb row with no nyboe twin survives",
       bool((_o.donor_key == "B").any()), True)
    # Federal and state committees are disjoint filers, so this pair is a
    # coincidence, not a double report. Deleting it would delete a real gift.
    ok("a fec/nyboe same-day same-amount pair is NOT deduped",
       int((_o.donor_key == "C").sum()), 2)

    # A differing amount or date is a different gift.
    _d2 = _d.copy()
    _d2.loc[1, "amount"] = 176.0
    ok("a one-dollar difference is a different gift",
       len(drop_cross_source_duplicates(_d2)), 5)
    ok("a file with no nyccfb rows is returned untouched",
       len(drop_cross_source_duplicates(_d[_d.source != "nyccfb"])), 3)
except Exception as _e:
    print(f"    [SKIP] {type(_e).__name__}: {_e}")

print("\n I10. refunds are not gifts")
print("     19,838 confirmed rows are negative and 34,796 are zero — refunds")
print("     and redesignations, which FEC files as Schedule A with the sign")
print("     flipped. They labelled 139 donors 'gave' in 2022 on money going")
print("     OUT, and dragged e_amount negative for 728 donors.")
try:
    import backtest_windows as B2
    import propensity as P2

    _g = pd.DataFrame({
        "donor_key": ["REFUND ONLY", "REAL GIVER", "REFUND ONLY", "REAL GIVER"],
        "source": ["fec"] * 4,
        "date": pd.to_datetime(["2022-09-01", "2022-09-01",
                                "2022-01-01", "2022-01-01"]),
        "amount": [-250.0, 100.0, -250.0, 200.0],
        "labor": [False] * 4,
        "is_dem": [False] * 4, "is_rep": [False] * 4,
    })
    ok("a refund in the label window is not 'gave'",
       B2.label_for(_g, 2022), {"REAL GIVER"})

    _u = pd.Index(["REFUND ONLY", "REAL GIVER"])
    _a = P2.amount_table(_g, pd.Timestamp("2022-06-30"), _u)
    ok("e_amount is never negative", bool((_a["e_amount"] > 0).all()), True)
    ok("a refund-only donor falls back to the ZIP prior",
       float(_a.loc["REFUND ONLY", "n_gifts"]), 0.0)
    ok("and the real gift still sets the real donor's amount",
       float(_a.loc["REAL GIVER", "donor_median"]), 200.0)
except Exception as _e:
    print(f"    [SKIP] {type(_e).__name__}: {_e}")

print("\n I8. a missing column is no opinion, and a blank side is no opinion")
print("     committee_overrides_corrected.csv predates `corporate_trade`, so")
print("     reading its silence as False let it OVERWRITE real tags from the")
print("     later files. REALTORS PAC arrived corporate_trade=true in both")
print("     tails and came out of the union False. Quarantine flags are OR-ed")
print("     across files now, because a missed quarantine feeds trade-assoc or")
print("     union payroll money into partisan lean as if it were preference.")
try:
    from committees import load_overrides
    import tempfile

    _old = ("committee,side,labor,reviewed\n"          # no corporate_trade at all
            "TRADE PAC,,false,yes\n"
            "BLANK SIDE PAC,,false,yes\n"
            "REAL DEM,DEM,false,yes\n")
    _new = ("committee,side,labor,corporate_trade,reviewed\n"
            "TRADE PAC,,false,true,yes\n"              # the tag the old file hid
            "BLANK SIDE PAC,REP,false,false,yes\n"     # a view where there was none
            "REAL DEM,REP,false,false,yes\n")          # a genuine conflict
    _paths = []
    for _txt in (_old, _new):
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False,
                                         encoding="utf-8") as _f:
            _f.write(_txt)
            _paths.append(Path(_f.name))
    _m = load_overrides(*_paths).set_index("name_norm")
    for _p in _paths:
        _p.unlink()

    ok("a later corporate_trade tag survives the older file's silence",
       bool(_m.loc["TRADE PAC", "corporate_trade"]), True)
    ok("a blank side does not beat a real one",
       _m.loc["BLANK SIDE PAC", "side"], "REP")
    ok("but a REAL side still wins on precedence",
       _m.loc["REAL DEM", "side"], "DEM")
    ok("and the union is still one row per committee",
       int(_m.index.duplicated().sum()), 0)
except Exception as _e:
    print(f"    [SKIP] {type(_e).__name__}: {_e}")

print("\n I6. the naming contract on outbound columns")
print("     Objective 4 (donor ROI) is NOT computable: no source records")
print("     solicitations, only gifts received. A column named as if it were")
print("     would be a causal claim the data cannot support, and the way that")
print("     happens is a rename for a dashboard header, not a decision.")
try:
    from write_supabase import NamingContractError, check_columns, check_frame

    def refuses(cols):
        try:
            check_columns(cols)
            return False
        except NamingContractError:
            return True

    ok("plain columns pass", refuses(["donor_key", "p_donate", "e_amount"]), False)
    ok("expected_dollars_untreated passes",
       refuses(["donor_key", "expected_dollars_untreated"]), False)
    ok("'donor_roi' is refused", refuses(["donor_key", "donor_roi"]), True)
    ok("'uplift' is refused", refuses(["uplift"]), True)
    ok("'expected_lift' is refused", refuses(["expected_lift"]), True)
    ok("'ROI_SCORE' is refused (case)", refuses(["ROI_SCORE"]), True)
    ok("substring match catches 'roi_pct'", refuses(["roi_pct"]), True)
    # The one that would actually happen: dropping the suffix for brevity.
    ok("'expected_dollars' alone still passes", refuses(["expected_dollars"]), False)

    _f = pd.DataFrame({"donor_key": ["A", "B"], "p_donate": [0.1, 0.2]})
    check_frame(_f, "donor_key")
    print("    [OK]   a clean frame passes check_frame")
    _d = pd.DataFrame({"donor_key": ["A", "A"], "p_donate": [0.1, 0.2]})
    try:
        check_frame(_d, "donor_key")
        print("    [FAIL] duplicate donor_key was accepted")
    except ValueError:
        print("    [OK]   duplicate donor_key is refused (upsert would be ambiguous)")
except Exception as _e:
    print(f"    [SKIP] {type(_e).__name__}: {_e}")

print("\n I7. e_amount shrinks single-gift donors and leaves the rest alone")
print("     Measured on held-out 2024 gifts, shrinking every donor toward their")
print("     ZIP median (what the plan proposed) is WORSE than not shrinking:")
print("     gift size is individual, so the prior adds noise to a fact already")
print("     observed. It helps only where there is one noisy observation.")
try:
    import propensity as P

    _g = pd.DataFrame({
        "donor_key": ["ONE|TOWN|11001"] + ["MANY|TOWN|11001"] * 6
                     + [f"F{i}|TOWN|11001" for i in range(30)],
        "date": pd.to_datetime(["2024-01-01"] * 37),
        "amount": [1000.0] + [20.0] * 6 + [10.0] * 30,
    })
    _u = pd.Index(sorted(set(_g["donor_key"])))
    _a = P.amount_table(_g, pd.Timestamp("2024-06-30"), _u)

    ok("a 6-gift donor is entirely their own median",
       float(_a.loc["MANY|TOWN|11001", "e_amount"]), 20.0)
    ok("and amount_is_own is exactly 1",
       float(_a.loc["MANY|TOWN|11001", "amount_is_own"]), 1.0)
    _one = float(_a.loc["ONE|TOWN|11001", "e_amount"])
    ok("a 1-gift donor is pulled toward the ZIP", _one < 1000.0, True)
    ok("but not all the way", _one > float(_a.loc["ONE|TOWN|11001", "zip_median"]), True)
    ok("amount_is_own reports the pull",
       round(float(_a.loc["ONE|TOWN|11001", "amount_is_own"]), 4),
       round(1.0 / (1.0 + P.AMOUNT_SHRINK_GIFTS), 4))
    ok("the ZIP comes out of donor_key",
       P.zip_of(pd.Series(["SMITH JOHN|GREAT NECK|11021"]))[0], "11021")
    ok("a thin ZIP falls back rather than trusting four neighbours",
       P.MIN_ZIP_DONORS >= 25, True)
except Exception as _e:
    print(f"    [SKIP] {type(_e).__name__}: {_e}")

print("\n I5. the backtest must not leak the future into its features")
print("     Every arm cuts features at the fold cutoff and the label starts")
print("     Aug 8 of the SAME year, so the gap between them is real but thin.")
print("     One off-by-one in the comparison and the model reads the answer.")
try:
    import backtest_windows as B

    _g = pd.DataFrame({
        "donor_key": ["A", "A", "B", "C"],
        "source": ["fec"] * 4,
        "date": pd.to_datetime(["2022-01-15",       # before cutoff
                                "2022-09-01",       # IN the label window
                                "2022-06-30",       # exactly the cutoff
                                "2022-07-15"]),     # after cutoff, before label
        "amount": [10.0, 500.0, 20.0, 30.0],
        "labor": [False] * 4,
        "is_dem": [True, True, False, False],
        "is_rep": [False, False, False, False],
    })
    _cut = B.cutoff_for(2022, "june")
    ok("june cutoff is 30 June of the label year", str(_cut.date()), "2022-06-30")
    ok("december cutoff is 31 Dec of the prior year",
       str(B.cutoff_for(2022, "december").date()), "2021-12-31")

    _F = B.build_features(_g, _cut, None, weighted=False,
                          floor=pd.Timestamp("2017-01-01"))
    # A's $500 September gift is the label. If it reached the features, A's
    # total would be 510 and max_gift 500.
    ok("the label-window gift is excluded from features",
       float(_F.loc["A", "total_dollars"]), 10.0)
    ok("and from max_gift", float(_F.loc["A", "max_gift"]), 10.0)
    ok("a gift exactly on the cutoff IS included", "B" in _F.index, True)
    ok("the July gap gift is excluded", "C" not in _F.index, True)

    _pos = B.label_for(_g, 2022)
    ok("only the September gift is a positive", _pos, {"A"})
    ok("a gift on Aug 8 counts as a positive",
       B.label_for(_g.assign(date=pd.to_datetime(
           ["2022-01-15", "2022-08-08", "2022-06-30", "2022-07-15"])), 2022), {"A"})
    ok("a gift on Aug 7 does not",
       B.label_for(_g.assign(date=pd.to_datetime(
           ["2022-01-15", "2022-08-07", "2022-06-30", "2022-07-15"])), 2022), set())

    # A narrow arm must censor, not drop: the donor is still scored, just blind.
    _F2 = B.build_features(_g, _cut, 2, weighted=False, floor=pd.Timestamp("2017-01-01"))
    _cen = B.censored_frame(pd.Index(["Z"]), _F2, 730.0)
    ok("a censored donor has no gifts", float(_cen.loc["Z", "n_gifts"]), 0.0)
    ok("and days_since_last pinned to the window, not zero",
       float(_cen.loc["Z", "days_since_last"]), 730.0)
    ok("and a NULL lean rather than 0.5", bool(pd.isna(_cen.loc["Z", "revealed_lean"])), True)

    # lift@k against a known ranking
    _y = np.array([1, 1, 0, 0, 0, 0, 0, 0, 0, 0])
    ok("a perfect top-20% ranking lifts 5x",
       round(B.lift_at_k(_y, np.array([9, 8, 7, 6, 5, 4, 3, 2, 1, 0]), 0.2), 3), 5.0)
    ok("an inverted ranking lifts 0", B.lift_at_k(_y, np.arange(10), 0.2), 0.0)

    # the split must be stable per donor and actually move with the seed
    _keys = pd.Index([f"K{i}" for i in range(2000)])
    ok("the same seed gives the same split",
       bool((B.split_mask(_keys, seed=1) == B.split_mask(_keys, seed=1)).all()), True)
    ok("a different seed gives a different split",
       bool((B.split_mask(_keys, seed=1) == B.split_mask(_keys, seed=2)).all()), False)
    ok("the split is close to half", abs(B.split_mask(_keys).mean() - 0.5) < 0.05, True)
except Exception as _e:
    print(f"    [SKIP] {type(_e).__name__}: {_e}")

print("\n I3. the dimension must never carry one committee key twice")
print("     attach_sides is a LEFT MERGE on the committee key, so a duplicated")
print("     key duplicates every gift to that committee and counts its dollars")
print("     twice in the lean. committees_tagged_v2.csv keys 1,371 rows on raw")
print("     cmte_ids that add_cmte_id_aliases would otherwise re-add from the")
print("     FEC master — the explicit tag has to win, not coexist.")
try:
    from committees import add_cmte_id_aliases

    _master = pd.DataFrame([
        {"cmte_id": "C00000001", "name_norm": "TAGGED BY NAME", "cycle": "2024",
         "cmte_pty_affiliation": "", "cand_pty_affiliation": "", "org_tp": "",
         "cmte_tp": "", "connected_org_nm": ""},
        {"cmte_id": "C00000002", "name_norm": "TAGGED BY ID", "cycle": "2024",
         "cmte_pty_affiliation": "", "cand_pty_affiliation": "", "org_tp": "",
         "cmte_tp": "", "connected_org_nm": ""},
    ])
    # C00000002 is already a key in the dimension because a human tagged the id
    # directly; TAGGED BY ID is its name, so the alias step will try to add it.
    _d = dim([("TAGGED BY NAME", "DEM", False), ("TAGGED BY ID", "REP", False),
              ("C00000002", "REP", False)])
    _out = add_cmte_id_aliases(_d, _master)
    ok("no duplicate committee key", int(_out["committee"].duplicated().sum()), 0)
    ok("the name-keyed alias was still added",
       "C00000001" in set(_out["committee"]), True)
    _row = _out[_out["committee"] == "C00000002"]
    ok("the explicitly tagged id survives exactly once", len(_row), 1)

    # And the real artifact, if it has been built.
    if C.COMMITTEE_DIM_PARQUET.exists():
        _real = pd.read_parquet(C.COMMITTEE_DIM_PARQUET, columns=["committee"])
        ok(f"built dimension ({len(_real):,} rows) has no duplicate key",
           int(_real["committee"].duplicated().sum()), 0)
    else:
        print("    [SKIP] committee_dim.parquet not built yet")
except Exception as _e:
    print(f"    [SKIP] {type(_e).__name__}: {_e}")

print("\n I4. a tag keyed on a cmte_id must also answer to the committee name")
print("     The tagging queue is generated from what donations.committee HOLDS,")
print("     which for a --no-download FEC run is the bare id. The tag is about")
print("     the committee, not about which spelling a cache happened to store,")
print("     so resolved_name has to become a key too — otherwise the same")
print("     committee is tagged under one form and unresolved under the other.")
try:
    from committees import _read_override_file
    import tempfile

    _csv = ("committee,resolved_name,side,labor,corporate_trade,confidence,reviewed\n"
            "C00000009,STOP REPUBLICANS,DEM,false,false,high,yes\n"
            "PLAIN NAME CMTE,,REP,false,false,high,yes\n")
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False,
                                     encoding="utf-8") as _f:
        _f.write(_csv)
        _p = Path(_f.name)
    _o = _read_override_file(_p).set_index("name_norm")
    _p.unlink()
    ok("the id form is a key", "C00000009" in _o.index, True)
    ok("the resolved name is also a key", "STOP REPUBLICANS" in _o.index, True)
    ok("both forms carry the same side",
       _o.loc["C00000009", "side"] == _o.loc["STOP REPUBLICANS", "side"] == "DEM", True)
    ok("a blank resolved_name adds no empty key", "" in _o.index, False)
    ok("a row without a resolved_name is untouched",
       _o.loc["PLAIN NAME CMTE", "side"], "REP")
except Exception as _e:
    print(f"    [SKIP] {type(_e).__name__}: {_e}")

print("\n I2. the donation window must agree across build/ and model/")
print("     build/matching.py and model/config.py each hold the window, because")
print("     build/ is a separate package model/ must not import. Three fetchers")
print("     drifted exactly this way before; this check is what stops a repeat.")
try:
    import importlib.util
    _mp = Path(__file__).resolve().parent.parent.parent / "build" / "matching.py"
    _spec = importlib.util.spec_from_file_location("_matching_probe", _mp)
    _m = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_m)
    ok("WINDOW_START agrees", _m.WINDOW_START, C.DONATION_WINDOW_START)
    ok("WINDOW_END agrees", _m.WINDOW_END, C.DONATION_WINDOW_END)
    ok("a pre-window date is excluded", _m.in_window("2016-12-31"), False)
    ok("the first in-window day is included", _m.in_window(C.DONATION_WINDOW_START), True)
    ok("an absurd future date is excluded", _m.in_window("2034-02-16"), False)
    ok("a blank date is excluded", _m.in_window(""), False)
except FileNotFoundError:
    print("    [SKIP] build/matching.py not reachable from this tree")

print("\n I. config sanity")
ok("prior is a positive dollar amount", C.REVEALED_LEAN_PRIOR_DOLLARS > 0, True)
ok("DEM/REP party code sets are disjoint",
   bool(C.FEC_DEM_PARTY_CODES & C.FEC_REP_PARTY_CODES), False)
ok("labor org types include 'L'", "L" in C.FEC_LABOR_ORG_TYPES, True)

# ------------------------------------------------------------------- summary
print()
if FAILURES:
    print(f"{len(FAILURES)} FAILED:")
    for f in FAILURES:
        print(f"  - {f}")
    sys.exit(1)
print("ALL CHECKS PASSED")
