#!/usr/bin/env python3
"""committees.py — committee -> (partisan side, labor flag) dimension.

Turns `donations.committee`, which is free text, into something a lean can be
computed over. Without this the only partisan tagging in the codebase is a
two-element hardcoded set (config.DEM_CONDUITS / REP_CONDUITS).

Keyed on the NORMALIZED COMMITTEE NAME, not cmte_id. That is forced, not
chosen: build/fetch_fec_bulk.py resolves cmte_id to a name and then drops the
id before the DB write, so the name is the only join key that survives into
`donations`. Measured 2026-08-08, the name join hits 99.8% of confirmed FEC
gifts, so this costs almost nothing in coverage -- but it does mean two
committees sharing a name collapse, which report_conflicts() surfaces rather
than hides.

Resolution ladder, highest precedence first:

  1. reviewed override      human judgement, model/donations/committee_overrides.csv
  2. CMTE_PTY_AFFILIATION   FEC's own field on the committee master
  3. CAND_PTY_AFFILIATION   via CAND_ID into the candidate master
  4. conduit override       config.DEM_CONDUITS / REP_CONDUITS
  5. unreviewed override    auto-proposed tags, applied only where nothing above fired
  6. NULL                   do not guess

Rung 5 sits below FEC on purpose: an unreviewed regex proposal must never
override an authoritative federal field, but it is better than nothing on the
NY BOE side where no federal field exists.

Why the override file is load-bearing rather than a fallback, measured against
production 2026-08-08:

  - FEC master name-join covers 99.8% of FEC gifts, but FEC assigns a party to
    only 38.6% of them. The remaining 222,859 gifts are hybrid and
    non-connected PACs (types V/N/Q/W) that FEC simply does not classify --
    ActBlue and WinRed among them.
  - NY BOE is 65% of all confirmed gifts and gets 0.1% coverage from the
    federal master, because state and local committees are not in a federal
    file at all.
  - Giving is concentrated enough to make hand-tagging tractable: the top 300
    committees carry 77.7% of confirmed gifts.

LABOR IS NOT A SIDE. Union COPE money is payroll deduction -- it signals
membership, not partisanship. The largest single committee in the file is a
teachers COPE PAC (105,291 gifts), and 16,029 registered Republicans gave $6.3M
to nominally Democratic-side committees, much of it exactly this. So `labor`
is a separate column, and donor_value.py excludes labor dollars from the lean
denominator rather than counting them as Democratic.

Usage:
    python model/donations/committees.py                    # build + report
    python model/donations/committees.py --report-only      # no write
"""
import argparse
import hashlib
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config as C  # noqa: E402
from persons_io import write_stamped  # noqa: E402

SIDE_DEM, SIDE_REP = "DEM", "REP"


def normalize_name(s: pd.Series) -> pd.Series:
    """The exact normalization the donations join uses: upper + collapse space.

    Deliberately minimal. Anything cleverer (punctuation stripping, suffix
    handling) would have to be applied identically at query time in the app and
    in SQL, and a normalization that lives in three places drifts.
    """
    return (s.fillna("").astype(str)
             .str.upper().str.replace(r"\s+", " ", regex=True).str.strip())


def source_fingerprint(*paths: Path) -> str:
    """Hash the inputs, so a stale dimension is detectable.

    persons_io.population_fingerprint hashes the person table, which is the
    right staleness key for anything derived from voters. This artifact has no
    voter dependency at all -- it is derived from the FEC masters and the
    override file -- so it stamps those instead.
    """
    h = hashlib.blake2b(digest_size=16)
    for p in paths:
        h.update(p.read_bytes() if p.exists() else b"<missing>")
    return h.hexdigest()


# ------------------------------------------------------------------- loading

def load_fec_master(path: Path = None) -> pd.DataFrame:
    """One row per (cycle, committee) from build/fetch_fec_committees.py."""
    path = Path(path or C.FEC_COMMITTEES_CSV)
    if not path.exists():
        raise SystemExit(
            f"{path} not found — run: python build/fetch_fec_committees.py")
    df = pd.read_csv(path, dtype=str).fillna("")
    df["name_norm"] = normalize_name(df["cmte_nm"])
    return df[df["name_norm"] != ""]


OVERRIDE_COLS = ["name_norm", "side", "labor", "corporate_trade", "reviewed", "confidence"]

# A hand tag marked low-confidence is a guess. It should not outrank FEC's own
# CMTE_PTY_AFFILIATION / CAND_PTY_AFFILIATION, which are filed by the committee
# itself. High and medium tags still sit at the top of the ladder, because there
# the human checked a source and the federal field is blank for most PACs.
#
# Deliberately applies to SIDE ONLY. The labor and corporate/trade quarantine
# flags are honoured at any confidence: quarantining errs toward "this money is
# not partisan evidence", which is the safe direction to be wrong in.
CONFIDENT = {"high", "medium"}


def _read_override_file(path: Path) -> pd.DataFrame:
    """One hand-tagged file, normalized to OVERRIDE_COLS.

    Tolerant about which optional columns exist: the 300-row reviewed file
    predates `corporate_trade`, the long-tail files have it.

    A file may also carry `resolved_name`, which committees_tagged_v2.csv does:
    1,371 of its rows are keyed on a raw cmte_id because that is what
    `donations.committee` holds for FEC rows written by a --no-download run, and
    the human tagged what they could see. The tag is about the COMMITTEE, not
    about which spelling of it a cache happens to store, so both keys are
    emitted and either one will join.
    """
    df = pd.read_csv(path, dtype=str).fillna("")
    out = pd.DataFrame({"name_norm": normalize_name(df["committee"])})
    side = df["side"].str.upper() if "side" in df else ""
    out["side"] = side.where(side.isin([SIDE_DEM, SIDE_REP]), "") if len(df) else ""
    # A MISSING COLUMN IS NO OPINION, NOT A NEGATIVE, so it lands NA rather than
    # False. committee_overrides_corrected.csv predates `corporate_trade`
    # entirely, and reading its silence as False let it overwrite real tags from
    # the later files -- REALTORS PAC arrived corporate_trade=true in both tails
    # and came out of the union False, un-quarantining 1,919 gifts of trade-
    # association money into partisan lean. See load_overrides for the merge.
    for col in ("labor", "corporate_trade"):
        out[col] = (df[col].str.lower().eq("true").astype("boolean") if col in df
                    else pd.Series(pd.NA, index=df.index, dtype="boolean"))
    out["reviewed"] = df["reviewed"].str.lower().eq("yes") if "reviewed" in df else False
    # Missing confidence is treated as "high": the 300-row file predates the
    # column on some rows, and those were the most carefully checked.
    out["confidence"] = (df["confidence"].str.lower().replace("", "high")
                         if "confidence" in df else "high")

    if "resolved_name" in df.columns:
        alias = out.copy()
        alias["name_norm"] = normalize_name(df["resolved_name"])
        # keep='first' == the id-keyed row wins if a name resolves twice, which
        # happens 16 times where two cmte_ids share a committee name.
        out = pd.concat([out, alias], ignore_index=True)

    return out[out["name_norm"] != ""].drop_duplicates("name_norm", keep="first")


def load_overrides(*paths: Path) -> pd.DataFrame:
    """Union every hand-tagged file, EARLIER paths winning on conflict.

    Order is the whole point. The files are complementary, not alternatives:
    each tagging queue was generated as "what still needs a tag", so it excluded
    by construction everything already resolved — 277 of the original 300 are
    absent from committees_to_tag.csv, and those 277 carry 767,816 gifts
    including ActBlue, WinRed and the two largest labor PACs. Loading a tail
    alone would drop them silently, so the reviewed file is passed first.

    Measured across all three files, the tie-break never actually fires on side:
    the 300 share no sided key with either tail, and the two tails share exactly
    one, which agrees. Order still matters for the labor and corporate/trade
    flags, and it is the cheap thing to get right if a fourth pass ever does
    disagree.
    """
    paths = [Path(p) for p in (paths or (C.COMMITTEE_OVERRIDES_CSV,
                                         C.COMMITTEE_TAGGED_CSV,
                                         C.COMMITTEE_TAGGED_V2_CSV))]
    frames = []
    for p in paths:
        if not p.exists():
            print(f"  NOTE: no override file at {p}")
            continue
        f = _read_override_file(p)
        frames.append(f)
        print(f"  {p.name}: {len(f):,} rows "
              f"({int((f.side != '').sum()):,} sided, {int(f.labor.sum()):,} labor, "
              f"{int(f.corporate_trade.sum()):,} corporate/trade)")
    if not frames:
        return pd.DataFrame(columns=OVERRIDE_COLS)

    for i, f in enumerate(frames):
        f["_file"] = i
        f["_blank_side"] = f["side"].eq("")
    merged = pd.concat(frames, ignore_index=True)
    before = len(merged)

    # THE QUARANTINE FLAGS ARE OR-ED ACROSS FILES, NOT OVERWRITTEN. `side` is a
    # judgement about which party a committee backs, so precedence is the right
    # rule and the earlier reviewer wins. `labor` and `corporate_trade` are not
    # judgements of that kind -- they say "this money is not evidence of
    # partisan preference", and the two costs are wildly asymmetric. A wrong
    # quarantine loses one committee's signal; a missed one feeds union payroll
    # deduction or access-seeking trade money into the lean as if it were
    # preference, which is the failure the whole split exists to prevent. So if
    # any reviewer flagged it, it stays flagged.
    #
    # This also makes a file that predates a column harmless: pd.NA is skipped
    # rather than counted as False.
    flags = merged.groupby("name_norm")[["labor", "corporate_trade"]].max()

    # A BLANK SIDE IS NOT AN OPINION EITHER, so it does not get to win on
    # precedence. Sorting sided rows ahead of blank ones -- stably, so file
    # order still decides between two real opinions -- means "earlier file wins"
    # reads as "the earliest reviewer WHO HAD A VIEW wins". Without this, a
    # committee left blank in the 300-row file stayed unresolved forever even
    # after a later pass tagged it.
    merged = merged.sort_values(["_blank_side", "_file"], kind="stable")

    # keep='first' == earliest opinionated file wins, which is why order matters.
    merged = merged.drop_duplicates("name_norm", keep="first").set_index("name_norm")
    for col in ("labor", "corporate_trade"):
        was = merged[col].reindex(merged.index).fillna(False).astype(bool).to_numpy()
        now = flags[col].reindex(merged.index).fillna(False).astype(bool).to_numpy()
        gained = int((now & ~was).sum())
        merged[col] = now
        if gained:
            print(f"  {gained:,} committees gained `{col}` from a later file "
                  f"(OR-ed, not overwritten)")
    merged = merged.reset_index()

    if before != len(merged):
        print(f"  {before - len(merged):,} names appeared in more than one file; "
              f"kept the earlier (reviewed) side")
    print(f"  union: {len(merged):,} committees")
    return merged[OVERRIDE_COLS]


# ---------------------------------------------------------------- resolution

def collapse_cycles(master: pd.DataFrame) -> pd.DataFrame:
    """One row per committee name, taking the most recent cycle's fields.

    Later cycles win, but a blank never overwrites a known value -- a committee
    that carried a party in 2020 and reports blank in 2026 keeps the 2020 party.
    """
    m = master.sort_values("cycle")
    out = {}
    for r in m.itertuples(index=False):
        prev = out.get(r.name_norm, {})
        out[r.name_norm] = {
            "name_norm": r.name_norm,
            "cmte_pty": r.cmte_pty_affiliation or prev.get("cmte_pty", ""),
            "cand_pty": r.cand_pty_affiliation or prev.get("cand_pty", ""),
            "org_tp":   r.org_tp or prev.get("org_tp", ""),
            "cmte_tp":  r.cmte_tp or prev.get("cmte_tp", ""),
            "connected_org": r.connected_org_nm or prev.get("connected_org", ""),
        }
    return pd.DataFrame(out.values())


def report_conflicts(master: pd.DataFrame) -> int:
    """Names carrying different parties under different cmte_ids.

    Printed, never silently resolved. A non-trivial count is the signal that
    the name key has outlived its usefulness and `donations` should start
    persisting cmte_id.
    """
    seen = master[master["cmte_pty_affiliation"] != ""]
    g = seen.groupby("name_norm")["cmte_pty_affiliation"].nunique()
    conflicted = g[g > 1]
    if len(conflicted):
        print(f"\n  WARNING: {len(conflicted):,} committee names carry conflicting "
              f"party across cmte_ids/cycles.")
        print("    Collapsed to the most recent non-blank. Examples:")
        for nm in list(conflicted.index)[:5]:
            vals = sorted(seen[seen["name_norm"] == nm]["cmte_pty_affiliation"].unique())
            print(f"      {nm[:58]:<58} {vals}")
        print("    If this count matters to you, persist cmte_id through the ETL.")
    return len(conflicted)


def _side_from_code(code: str) -> str:
    if code in C.FEC_DEM_PARTY_CODES:
        return SIDE_DEM
    if code in C.FEC_REP_PARTY_CODES:
        return SIDE_REP
    return ""


def build_dimension(master: pd.DataFrame, overrides: pd.DataFrame) -> pd.DataFrame:
    """Apply the ladder. Returns one row per committee name with provenance.

    `resolved_by` is not decoration: it is how you audit which rung is carrying
    the coverage, and therefore what breaks if a rung regresses.
    """
    collapsed = collapse_cycles(master)
    overrides = overrides.copy()
    # Tolerate an override frame that predates a column rather than requiring
    # every caller to know the current schema — the 300-row reviewed file was
    # written before corporate_trade existed.
    for _c, _default in (("side", ""), ("labor", False),
                         ("corporate_trade", False), ("reviewed", False),
                         ("confidence", "high")):
        if _c not in overrides.columns:
            overrides[_c] = _default
    df = collapsed.merge(overrides, on="name_norm", how="outer")
    for col in ("cmte_pty", "cand_pty", "org_tp", "cmte_tp", "connected_org", "side"):
        df[col] = df[col].fillna("")
    # via "boolean" (nullable) rather than a bare fillna: the outer merge leaves
    # object-dtype NaNs, and fillna on object dtype is a deprecated downcast.
    for _c in ("labor", "corporate_trade", "reviewed"):
        df[_c] = df[_c].astype("boolean").fillna(False).astype(bool)

    conduit_dem = {normalize_name(pd.Series([c]))[0] for c in C.DEM_CONDUITS}
    conduit_rep = {normalize_name(pd.Series([c]))[0] for c in C.REP_CONDUITS}

    sides, why = [], []
    for r in df.itertuples(index=False):
        s, rung = "", ""
        confident = str(r.confidence or "high").lower() in CONFIDENT
        # 1. A reviewed tag the human was confident about.
        if r.reviewed and r.side and confident:
            s, rung = r.side, "override-reviewed"
        # 2-3. FEC's own filings. These outrank a low-confidence hand tag: the
        # committee itself reported this, and a guess should not overrule it.
        if not s and (v := _side_from_code(r.cmte_pty)):
            s, rung = v, "fec-committee-party"
        if not s and (v := _side_from_code(r.cand_pty)):
            s, rung = v, "fec-candidate-party"
        # 4. The two conduits, which FEC leaves blank but which are unambiguous.
        if not s and r.name_norm in conduit_dem:
            s, rung = SIDE_DEM, "conduit"
        if not s and r.name_norm in conduit_rep:
            s, rung = SIDE_REP, "conduit"
        # 5. A reviewed but low-confidence tag — better than nothing, and this
        # is where most NY BOE committees land since no federal field covers them.
        if not s and r.reviewed and r.side:
            s, rung = r.side, "override-reviewed-low"
        # 6. An auto-proposed tag nobody has checked.
        if not s and r.side:
            s, rung = r.side, "override-unreviewed"
        sides.append(s or None)
        why.append(rung or "unresolved")

    df["side"] = sides
    df["resolved_by"] = why
    # Labor is orthogonal to side and quarantines the row downstream regardless
    # of which rung set a side -- a teachers COPE PAC can be tagged DEM by a
    # regex and still must not count as Democratic giving.
    df["labor"] = df["labor"] | df["org_tp"].isin(C.FEC_LABOR_ORG_TYPES)
    df["labor_source"] = df.apply(
        lambda r: "fec-org-tp" if r["org_tp"] in C.FEC_LABOR_ORG_TYPES
        else ("override" if r["labor"] else ""), axis=1)

    # Corporate and trade-association PACs, quarantined for the same reason as
    # labor: access-seeking money is not a partisan preference. FEC's own ORG_TP
    # already distinguishes C (corporation), T (trade association) and W
    # (corporation without capital stock), so the hand tags supplement it rather
    # than being the only source. Orthogonal to side exactly as labor is -- a
    # committee can be tagged REP and still be quarantined.
    df["corporate_trade"] = df["corporate_trade"] | df["org_tp"].isin({"C", "T", "W"})

    return df[["name_norm", "side", "resolved_by", "labor", "labor_source",
               "corporate_trade", "cmte_tp", "org_tp", "connected_org", "reviewed"]] \
             .rename(columns={"name_norm": "committee"}) \
             .sort_values("committee").reset_index(drop=True)


def add_cmte_id_aliases(dim: pd.DataFrame, master: pd.DataFrame) -> pd.DataFrame:
    """Also key the dimension on raw FEC committee IDs, not just names.

    `donations.committee` does not always hold a name. build/fetch_fec_bulk.py
    resolves cmte_id through the committee master and falls back to
    `.get(cmte_id, cmte_id)` — so when it runs with --no-download, which skips
    loading that master, it stores the bare `C00401224` id instead. On the
    2026-08-08 local run that was 100% of FEC rows: 1,350,314 confirmed gifts
    joined to nothing, and the untagged bucket read 80.6% instead of ~17%.

    Rather than forbid --no-download or re-run the match, the dimension simply
    answers to both key forms. Emitting alias rows keeps the join in
    donor_value.attach_sides a single unconditional merge.
    """
    ids = master[["cmte_id", "name_norm"]].drop_duplicates()
    ids = ids[ids["cmte_id"].str.match(r"^C\d{8}$", na=False)]
    alias = ids.merge(dim, left_on="name_norm", right_on="committee", how="inner")
    if alias.empty:
        return dim
    alias = alias.drop(columns=["committee", "name_norm"]).rename(columns={"cmte_id": "committee"})
    # A cmte_id is unique per committee, so a duplicate here means two master
    # rows disagreed; keep the first and let report_conflicts() surface it.
    alias = alias.drop_duplicates("committee")

    # AN ID ALREADY IN THE DIMENSION IS NOT AN ALIAS TO ADD -- it is a key
    # somebody tagged directly, and re-adding it would put TWO rows under one
    # committee key. attach_sides is a left merge on that key, so every gift to
    # such a committee would be duplicated and counted twice in the lean.
    # committees_tagged_v2.csv keys 1,371 rows on raw cmte_ids, all 1,371 of
    # which are in the master, so this is the normal case rather than an edge.
    # The explicit tag wins because it is the more specific statement.
    collision = alias["committee"].isin(set(dim["committee"]))
    if collision.any():
        print(f"  {int(collision.sum()):,} cmte_id(s) already tagged directly; "
              f"kept the explicit tag, did not duplicate the row")
        alias = alias[~collision]

    out = pd.concat([dim, alias[dim.columns]], ignore_index=True)
    print(f"  + {len(alias):,} cmte_id alias keys (for caches that stored raw ids)")
    assert not out["committee"].duplicated().any(), "duplicate committee key in dimension"
    return out.sort_values("committee").reset_index(drop=True)


def report(dim: pd.DataFrame) -> None:
    print(f"\n  committees in dimension        {len(dim):>10,}")
    print(f"  with a side                    {dim['side'].notna().sum():>10,}")
    print(f"  labor-tagged                   {int(dim['labor'].sum()):>10,}")
    print(f"  corporate/trade-tagged         {int(dim['corporate_trade'].sum()):>10,}")
    print("\n  side resolved by rung")
    for rung, n in dim["resolved_by"].value_counts().items():
        print(f"    {rung:<22} {n:>10,}")
    print("\n  NOTE: these are committee COUNTS. What matters is coverage weighted")
    print("  by gifts, which needs the donations table — donor_value.py reports it.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fec", type=Path, default=C.FEC_COMMITTEES_CSV)
    # All three hand-tag files, reviewed first so it wins on conflict.
    ap.add_argument("--overrides", type=Path, nargs="+",
                    default=[C.COMMITTEE_OVERRIDES_CSV, C.COMMITTEE_TAGGED_CSV,
                             C.COMMITTEE_TAGGED_V2_CSV])
    ap.add_argument("--out", type=Path, default=C.COMMITTEE_DIM_PARQUET)
    ap.add_argument("--report-only", action="store_true", help="build but do not write")
    args = ap.parse_args()

    print(f"Building committee dimension from {args.fec}")
    master = load_fec_master(args.fec)
    overrides = load_overrides(*args.overrides)
    print(f"  FEC master rows {len(master):,}   overrides {len(overrides):,} "
          f"({int(overrides['reviewed'].sum()) if len(overrides) else 0} reviewed)")

    report_conflicts(master)
    dim = build_dimension(master, overrides)
    dim = add_cmte_id_aliases(dim, master)
    report(dim)

    if args.report_only:
        print("\n[report only] nothing written.")
        return
    fp = source_fingerprint(Path(args.fec), *[Path(o) for o in args.overrides])
    write_stamped(dim, Path(args.out), fp, source="fec_master+overrides")
    print(f"\n  wrote {args.out}  ({len(dim):,} rows, fingerprint {fp[:12]}…)")


if __name__ == "__main__":
    main()
