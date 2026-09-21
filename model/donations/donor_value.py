#!/usr/bin/env python3
"""donor_value.py — per-donor revealed partisan lean, with labor money quarantined.

Stage one of objectives 3/4. Answers "which side does this person actually give
to", which the party model is currently bad at: measured 2026-08-08 against
production, unaffiliated donors who have ONLY ever given to Democratic
committees score a mean dem_lean_prob of 0.480 -- a coin flip -- while REP-only
givers score 0.277. 7,918 people with an unambiguous revealed preference are
being treated as undecided.

THE ONE RULE THAT MAKES THIS HONEST: labor dollars are not partisan dollars.
Union COPE money is payroll deduction -- it signals membership, not preference.
The largest single committee in the file is a teachers COPE PAC (105,291 gifts,
13,327 donors), 25.1% of all NY BOE giving is labor-tagged, and 16,029
registered Republicans gave $6.3M to nominally Democratic-side committees,
much of it exactly this. Counting it as Democratic support would invert the
signal for tens of thousands of people. So labor dollars are excluded from the
lean denominator entirely and surfaced as their OWN features -- union
membership is genuinely useful for targeting, it is just a different fact.

Shrinkage, not a raw ratio: one $10 gift must not read as a 1.0 lean.

    revealed_lean = (dem + prior/2) / (dem + rep + prior)

with prior = config.REVEALED_LEAN_PRIOR_DOLLARS. `revealed_lean_dollars` (the
unshrunk denominator) always ships alongside, because a lean of 0.55 on $40 and
a lean of 0.55 on $40,000 are not the same claim and a consumer that cannot
tell them apart will misuse both.

A donor who gives to both sides lands mid-scale WITH a large dollar weight,
which is a completely different object from a non-donor's NULL. `gave_both_sides`
is carried separately because access-seeking donors behave differently from
persuadable ones and should not be smoothed into the middle of one scale.

Usage:
    python model/donations/donor_value.py                 # build + report
    python model/donations/donor_value.py --report-only
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config as C  # noqa: E402
from persons_io import write_stamped  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from committees import normalize_name, source_fingerprint  # noqa: E402

SIDE_DEM, SIDE_REP = "DEM", "REP"


def drop_cross_source_duplicates(donations: pd.DataFrame) -> pd.DataFrame:
    """Drop nyccfb rows that are the same gift already reported through nyboe.

    NYC candidate committees register with the STATE Board of Elections and also
    participate in the CITY Campaign Finance Board programme, so one
    contribution gets filed twice and arrives here under two sources. Measured
    on the local run: 5,424 of 19,426 confirmed nyccfb rows -- 28% -- match an
    nyboe row on donor_key, exact date and exact amount, $9.5M in total.

    THE COMMITTEE STRING CANNOT BE USED TO CONFIRM THIS, which is why it went
    unnoticed. The two sources name the recipient differently on purpose: CFB
    stores the CANDIDATE and NY BOE the COMMITTEE, so the same gift reads
    "DIETL, RICHARD A" in one and "FRIENDS OF BO DIETL" in the other, and a
    string comparison scores them 0.6% identical. Matching the CFB candidate
    surname into the BOE committee name confirms 68.8% directly, and the
    remainder are mostly the same thing defeating a crude test -- hyphenated
    names ("MARK-VIVERITO, MELISSA" / "VIVERITO NYC"), reordered names
    ("TAN, ALISON" / "ALISON TAN FOR NEW YORK").

    Left undropped this inflates gift counts, dollar totals and the lean's
    evidence weight for NYC donors specifically, and invents a `n_sources` = 2
    signal that means nothing except that a donor gave in New York City.

    ONLY nyboe<->nyccfb IS DEDUPED. fec<->nyboe shares 2,462 triples and those
    are left alone: federal and state committees are disjoint filers, so a donor
    giving the same amount on the same day to one of each is a coincidence, not
    a double report. Deduping it would delete real gifts.
    """
    d = donations
    if not {"source", "donor_key", "donation_date", "amount"} <= set(d.columns):
        return d
    if not (d["source"] == "nyccfb").any():
        return d

    key = ["donor_key", "donation_date", "_amt"]
    d = d.copy()
    d["_amt"] = pd.to_numeric(d["amount"], errors="coerce").round(2)
    boe = d.loc[d["source"] == "nyboe", key].drop_duplicates()
    boe["_in_boe"] = True
    merged = d.merge(boe, on=key, how="left")
    drop = (merged["source"] == "nyccfb").to_numpy() & merged["_in_boe"].notna().to_numpy()
    n = int(drop.sum())
    if n:
        print(f"  {n:,} nyccfb rows dropped as duplicates of nyboe filings "
              f"(${merged.loc[drop, '_amt'].sum():,.0f})")
    out = merged[~drop].drop(columns=["_amt", "_in_boe"]).reset_index(drop=True)
    # The merge fans out if an nyboe row shares (donor, date, amount) with
    # another nyboe row, which would silently ADD rows while claiming to remove
    # them. drop_duplicates on the BOE side prevents it; this catches a
    # regression in that.
    assert len(out) <= len(d), "dedupe added rows"
    return out


def attach_sides(donations: pd.DataFrame, committee_dim: pd.DataFrame) -> pd.DataFrame:
    """Join the committee dimension onto donations by normalized name.

    Left join on purpose: a committee absent from the dimension gets side NaN
    and labor False, and is then excluded from the lean denominator by
    revealed_lean(). Absence of a tag is not evidence of neutrality -- it is
    absence of evidence, and it must not dilute a real signal toward 0.5.
    """
    d = donations.copy()
    d["committee_norm"] = normalize_name(d["committee"])
    dim = committee_dim.rename(columns={"committee": "committee_norm"})
    cols = ["committee_norm", "side", "labor"]
    if "corporate_trade" in dim.columns:
        cols.append("corporate_trade")
    out = d.merge(dim[cols], on="committee_norm", how="left")
    for c in ("labor", "corporate_trade"):
        if c not in out.columns:
            out[c] = False
        out[c] = out[c].astype("boolean").fillna(False).astype(bool)
    return out


def eligible_mask(d: pd.DataFrame) -> pd.Series:
    """Rows that may enter the partisan lean: confirmed, sided, and NOT labor.

    `confirmed` is non-negotiable. A "possible" match agrees on surname block
    and name tokens but has NO address agreement, so it is a different person
    until proven otherwise -- and POSSIBLE_CAP=10 means one real contribution
    can be replicated across every same-surname voter on the block.
    """
    m = (d["confirmed"].astype(bool)
         & d["side"].isin([SIDE_DEM, SIDE_REP])
         & ~d["labor"].astype(bool))
    # Corporate and trade-association PACs are quarantined alongside labor:
    # REALTORS PAC, METLIFE, CITIGROUP and VERIZON give to incumbents of both
    # parties by design, so their money says "access", not "preference".
    if C.QUARANTINE_CORPORATE_TRADE and "corporate_trade" in d.columns:
        m &= ~d["corporate_trade"].astype(bool)
    return m


def revealed_lean(donations: pd.DataFrame, committee_dim: pd.DataFrame, *,
                  prior_dollars: float = None) -> pd.DataFrame:
    """One row per donor_key: revealed_lean, its dollar weight, and labor features.

    Returns NULL lean (not 0.5) for donors with no eligible partisan dollars.
    That distinction is the whole point: 0.5 means "gives to both sides", NULL
    means "we have no partisan evidence", and collapsing them would tell a
    canvasser that a union member with no partisan giving is a swing donor.
    """
    prior = C.REVEALED_LEAN_PRIOR_DOLLARS if prior_dollars is None else prior_dollars
    d = attach_sides(donations, committee_dim)
    d["amount"] = pd.to_numeric(d["amount"], errors="coerce").fillna(0.0)

    elig = d[eligible_mask(d)]
    dem = elig[elig["side"] == SIDE_DEM].groupby("donor_key")["amount"].sum()
    rep = elig[elig["side"] == SIDE_REP].groupby("donor_key")["amount"].sum()
    # Gift COUNTS alongside dollars, because the two sides give in different
    # denominations: median lifetime partisan giving runs $24 for DEM-only
    # donors against $250 for REP-only ones (small-dollar ActBlue traffic vs
    # larger direct gifts). Any dollar-denominated statistic inherits that
    # asymmetry, so a count-denominated alternative has to be measurable
    # without re-deriving the whole frame.
    dem_n = elig[elig["side"] == SIDE_DEM].groupby("donor_key").size()
    rep_n = elig[elig["side"] == SIDE_REP].groupby("donor_key").size()

    conf = d[d["confirmed"].astype(bool)]
    lab = conf[conf["labor"]]
    labor_total = lab.groupby("donor_key")["amount"].sum()
    labor_n = lab.groupby("donor_key").size()

    keys = sorted(set(dem.index) | set(rep.index) | set(labor_total.index))
    out = pd.DataFrame({"donor_key": keys})
    out["dem_dollars"] = out["donor_key"].map(dem).fillna(0.0)
    out["rep_dollars"] = out["donor_key"].map(rep).fillna(0.0)
    out["dem_gifts"] = out["donor_key"].map(dem_n).fillna(0).astype(int)
    out["rep_gifts"] = out["donor_key"].map(rep_n).fillna(0).astype(int)
    out["labor_pac_total"] = out["donor_key"].map(labor_total).fillna(0.0)
    out["labor_pac_n"] = out["donor_key"].map(labor_n).fillna(0).astype(int)

    denom = out["dem_dollars"] + out["rep_dollars"]
    out["revealed_lean_dollars"] = denom
    out["revealed_lean"] = np.where(
        denom > 0,
        (out["dem_dollars"] + prior / 2.0) / (denom + prior),
        np.nan)
    out["gave_both_sides"] = (out["dem_dollars"] > 0) & (out["rep_dollars"] > 0)
    return out


def report_untagged(donations: pd.DataFrame, committee_dim: pd.DataFrame,
                    top: int = 8) -> None:
    """The largest committees carrying no tag, by confirmed gift volume.

    This is the regression alarm for the whole dimension, and it exists because
    of a real near-miss. NY BOE truncates one committee name at 81 characters:
    205,904 confirmed gifts -- 11% of the file, the single largest labor PAC in
    it -- arrive as "...POLITICAL EDUCATION OF THE NEW YO". It is tagged today
    only because the person doing the tagging tagged the truncated string as
    well as the full one. A re-fetch that stopped truncating would un-tag all
    205,904 in silence, and since the tag is labor=True, that would push union
    payroll money straight back into partisan lean -- the exact failure the
    labor split exists to prevent.

    Nothing about the join would error. The only symptom is a big committee
    appearing here, so it is printed on every run.
    """
    d = attach_sides(donations, committee_dim)
    d = d[d["confirmed"].astype(bool)]
    untagged = d[d["side"].isna() & ~d["labor"] & ~d.get(
        "corporate_trade", pd.Series(False, index=d.index))]
    if untagged.empty:
        print("\n  every confirmed gift joins a tagged committee")
        return
    g = untagged.groupby("committee_norm").size().sort_values(ascending=False)
    share = 100.0 * len(untagged) / max(1, len(d))
    print(f"\n  largest UNTAGGED committees ({len(untagged):,} confirmed gifts, "
          f"{share:.1f}% of the file)")
    for name, n in g.head(top).items():
        print(f"    {n:>8,}  {name[:64]}")
    print("    a familiar name appearing here means a tag stopped joining, not "
          "that a\n    new committee arrived — check for a changed or "
          "un-truncated name first.")


def report(out: pd.DataFrame) -> None:
    has = out["revealed_lean"].notna()
    print(f"\n  donors with any tagged money    {len(out):>10,}")
    print(f"  with a partisan lean            {int(has.sum()):>10,}")
    print(f"  labor-only (lean is NULL)       {int((~has).sum()):>10,}")
    print(f"  gave to both sides              {int(out['gave_both_sides'].sum()):>10,}")
    if has.any():
        lean = out.loc[has, "revealed_lean"]
        print(f"\n  lean distribution ({int(has.sum()):,} donors)")
        for lo, hi, lbl in [(0.0, .1, "0.0-0.1  hard REP"), (.1, .3, "0.1-0.3"),
                            (.3, .7, "0.3-0.7  mixed"), (.7, .9, "0.7-0.9"),
                            (.9, 1.01, "0.9-1.0  hard DEM")]:
            n = int(((lean >= lo) & (lean < hi)).sum())
            print(f"    {lbl:<20} {n:>9,}  {100.0*n/len(lean):>5.1f}%")
        print(f"\n  median evidence weight          "
              f"${out.loc[has, 'revealed_lean_dollars'].median():>9,.0f}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--donations", type=Path, default=C.CACHE / "donations.parquet",
                    help="cached donations (model/refresh_cache.py writes this)")
    ap.add_argument("--dim", type=Path, default=C.COMMITTEE_DIM_PARQUET)
    ap.add_argument("--out", type=Path, default=C.ARTIFACTS / "donor_lean.parquet")
    ap.add_argument("--report-only", action="store_true")
    args = ap.parse_args()

    if not args.donations.exists():
        raise SystemExit(f"{args.donations} not found — run model/refresh_cache.py")
    if not args.dim.exists():
        raise SystemExit(f"{args.dim} not found — run model/donations/committees.py")

    print(f"Loading {args.donations}")
    donations = pd.read_parquet(args.donations,
                                columns=["donor_key", "source", "donation_date",
                                         "amount", "committee", "confirmed"])
    dim = pd.read_parquet(args.dim)
    donations = drop_cross_source_duplicates(donations)
    print(f"  {len(donations):,} donation rows, {len(dim):,} committees in dimension")

    out = revealed_lean(donations, dim)
    report(out)
    report_untagged(donations, dim)

    if args.report_only:
        print("\n[report only] nothing written.")
        return
    fp = source_fingerprint(args.donations, args.dim)
    write_stamped(out, args.out, fp, source="donations+committee_dim")
    print(f"\n  wrote {args.out}  ({len(out):,} donors)")


if __name__ == "__main__":
    main()
