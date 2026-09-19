#!/usr/bin/env python3
"""audit_donations.py — measure donation coverage and completeness by source and year.

Run this BEFORE and AFTER a re-pull. The point is not the absolute numbers, it
is the delta: without a baseline you cannot show what a re-pull actually added,
and "the numbers look bigger" is not a measurement.

Why it exists: as of 2026-08-08 the FEC side of `donations` has essentially no
history before 2023 (35-2,571 gifts/year for 2013-2022, then 97,593 in 2023 and
247,189 in 2024). That is not donor behaviour, it is a fetch artifact —
fetch_fec_bulk.py's --cycles defaults to [2024], so only the 2023-24 cycle was
ever downloaded. The tell is in section B below: the average OLDEST gift is
year ~2023 even for donors holding 1-9 records, who cannot have been truncated
by the API path's 30-record cap. Any comparison of history windows (all / 2020+
/ 4y / 2y) is meaningless until that is fixed, because today every window
differs only in its NY BOE content.

Sections:
    A  volume by source x year          -- the coverage picture
    B  truncation diagnostics           -- is thin history real or an artifact?
    C  duplicate rate                   -- `donations` has NO unique constraint
    D  date validity                    -- unusable rows, per source
    E  committee coverage               -- how much is classifiable at all
    F  donor overlap                    -- confirmed vs possible-only

Run from repo root:
    python build/audit_donations.py
    python build/audit_donations.py --json out.json   # machine-readable, for diffing
"""
import argparse
import json
import os
from pathlib import Path

import psycopg2

ROOT = Path(__file__).resolve().parent.parent
STATEMENT_TIMEOUT = "15min"

# Below this, a year's rows cannot support a training window even if present.
THIN_YEAR_GIFTS = 5_000


def connect():
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env.local")
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass
    url = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DSN")
    if not url:
        raise SystemExit("DATABASE_URL not set — add it to .env.local or export it.")
    return psycopg2.connect(url)


def q(cur, sql, params=None):
    cur.execute(sql, params or ())
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def table(rows, cols, widths=None):
    if not rows:
        print("    (no rows)")
        return
    widths = widths or [max(len(c), 12) for c in cols]
    print("    " + "  ".join(c.rjust(w) for c, w in zip(cols, widths)))
    for r in rows:
        cells = []
        for c, w in zip(cols, widths):
            v = r.get(c)
            if isinstance(v, float):
                s = f"{v:,.1f}"
            elif isinstance(v, int):
                s = f"{v:,}"
            else:
                s = str(v) if v is not None else "-"
            cells.append(s.rjust(w))
        print("    " + "  ".join(cells))


# ------------------------------------------------------------------ sections

def section_a(cur, out):
    """Volume by source x year. The headline coverage picture."""
    print("\n" + "=" * 78)
    print("A. VOLUME BY SOURCE x YEAR (confirmed only)")
    print("=" * 78)
    rows = q(cur, """
        SELECT source,
               extract(year from donation_date)::int AS yr,
               COUNT(*)::int                          AS gifts,
               COUNT(DISTINCT donor_key)::int         AS donors,
               ROUND(SUM(amount)::numeric, 0)::float  AS dollars
        FROM donations
        WHERE confirmed AND donation_date BETWEEN '2010-01-01' AND '2027-12-31'
        GROUP BY 1, 2 ORDER BY 1, 2
    """)
    for src in sorted({r["source"] for r in rows}):
        sub = [r for r in rows if r["source"] == src]
        print(f"\n  source = {src}")
        table(sub, ["yr", "gifts", "donors", "dollars"])
        thin = [r["yr"] for r in sub if r["gifts"] < THIN_YEAR_GIFTS]
        if thin:
            print(f"    thin years (<{THIN_YEAR_GIFTS:,} gifts): {thin}")
            print("      ^ too sparse to support a training window on this source alone.")
    out["volume"] = rows


def section_b(cur, out):
    """Is thin history real, or a truncation artifact?

    Two independent truncation mechanisms exist in the fetchers:
      - fetch_fec_bulk.py --cycles defaults to [2024]  -> no other cycle downloaded
      - fetch_fec.py uses per_page=30, sort=-date, no paging -> 30 most recent only

    They leave different fingerprints. The 30-cap shows as a spike at exactly 30
    gifts/donor. The cycle default shows as recent-only history even among
    donors well under the cap -- which is the diagnostic that matters, because
    it cannot be explained away by the cap.
    """
    print("\n" + "=" * 78)
    print("B. TRUNCATION DIAGNOSTICS (fec)")
    print("=" * 78)

    print("\n  gifts-per-donor near the 30-record API cap")
    cap = q(cur, """
        WITH g AS (SELECT donor_key, COUNT(*) n FROM donations
                   WHERE source = 'fec' AND confirmed GROUP BY 1)
        SELECT n::int AS gifts_per_donor, COUNT(*)::int AS donors
        FROM g WHERE n BETWEEN 27 AND 33 GROUP BY 1 ORDER BY 1
    """)
    table(cap, ["gifts_per_donor", "donors"])
    at30 = next((r["donors"] for r in cap if r["gifts_per_donor"] == 30), 0)
    nbr = [r["donors"] for r in cap if r["gifts_per_donor"] in (29, 31)]
    if nbr and at30 > 2 * (sum(nbr) / len(nbr)):
        print(f"    SPIKE at exactly 30 ({at30:,} vs ~{sum(nbr)//len(nbr):,} either side)")
        print("      -> the API path's per_page=30 cap is binding for these donors.")

    print("\n  oldest gift per donor, split by whether the 30-cap could apply")
    old = q(cur, """
        WITH g AS (SELECT donor_key, COUNT(*) n, MIN(donation_date) oldest
                   FROM donations
                   WHERE source = 'fec' AND confirmed AND donation_date > '2000-01-01'
                   GROUP BY 1)
        SELECT CASE WHEN n >= 30 THEN 'at/over 30 cap'
                    WHEN n >= 10 THEN '10-29 (under cap)'
                    ELSE '1-9 (well under cap)' END AS bucket,
               COUNT(*)::int AS donors,
               ROUND(AVG(extract(year from oldest))::numeric, 1)::float AS avg_oldest_year
        FROM g GROUP BY 1 ORDER BY 1
    """)
    table(old, ["bucket", "donors", "avg_oldest_year"])
    under = next((r for r in old if r["bucket"].startswith("1-9")), None)
    if under and under["avg_oldest_year"] and under["avg_oldest_year"] >= 2022.0:
        print(f"    Donors with 1-9 gifts average oldest year {under['avg_oldest_year']}.")
        print("      These CANNOT have been truncated by the 30-cap, so thin pre-2023")
        print("      history is the --cycles [2024] default, not real coverage.")
        print("      REMEDY: fetch_fec_bulk.py --cycles 2018 2020 2022 2024 2026")
    out["truncation"] = {"cap_histogram": cap, "oldest_by_bucket": old}


def section_c(cur, out):
    """Duplicate rate. `donations` has no unique constraint at all.

    migrate_donations_psycopg2.py says ON CONFLICT DO NOTHING, which is a no-op
    without a constraint to conflict on -- dedup relies entirely on its TRUNCATE.
    Separately, fetch_fec.py does NOT uppercase city while fetch_fec_bulk.py
    does, and both write the same cache file, so one person can acquire two
    cache keys that collapse to one donor_key only at insert (.upper()) --
    double-counting their giving.
    """
    print("\n" + "=" * 78)
    print("C. DUPLICATE RATE (no unique constraint exists on donations)")
    print("=" * 78)
    rows = q(cur, """
        WITH d AS (
          SELECT source, donor_key, donation_date, amount, committee, COUNT(*) n
          FROM donations WHERE confirmed
          GROUP BY 1,2,3,4,5 HAVING COUNT(*) > 1)
        SELECT source,
               COUNT(*)::int                    AS dup_groups,
               SUM(n - 1)::int                  AS excess_rows,
               ROUND(SUM((n-1) * 1.0), 0)::float AS excess
        FROM d GROUP BY 1 ORDER BY 1
    """)
    table(rows, ["source", "dup_groups", "excess_rows"])
    tot = q(cur, "SELECT COUNT(*)::int n FROM donations WHERE confirmed")[0]["n"]
    excess = sum(r["excess_rows"] or 0 for r in rows)
    print(f"\n    confirmed rows {tot:,}; exact-duplicate excess {excess:,} "
          f"({100.0*excess/tot:.2f}%)")
    print("      Exact dupes are indistinguishable from two genuine same-day gifts")
    print("      of the same amount to the same committee, so this is an UPPER bound.")
    out["duplicates"] = {"by_source": rows, "confirmed_rows": tot, "excess": excess}


def section_d(cur, out):
    """Date validity. A dateless or absurd-dated gift cannot carry recency."""
    print("\n" + "=" * 78)
    print("D. DATE VALIDITY (recency features depend on this)")
    print("=" * 78)
    rows = q(cur, """
        SELECT source,
               COUNT(*) FILTER (WHERE donation_date IS NULL)::int          AS null_date,
               COUNT(*) FILTER (WHERE donation_date < '2000-01-01')::int   AS pre_2000,
               COUNT(*) FILTER (WHERE donation_date > now())::int          AS future_dated,
               COUNT(*)::int                                                AS total
        FROM donations WHERE confirmed GROUP BY 1 ORDER BY 1
    """)
    table(rows, ["source", "null_date", "pre_2000", "future_dated", "total"])
    print("\n    Rows without a usable date cannot contribute recency, frequency, or")
    print("    any windowed feature -- they are droppable, but must be COUNTED as")
    print("    dropped rather than silently excluded.")
    out["dates"] = rows


def section_e(cur, out):
    """How much giving is classifiable at all today, before the committee dimension."""
    print("\n" + "=" * 78)
    print("E. COMMITTEE COVERAGE (pre-dimension baseline)")
    print("=" * 78)
    rows = q(cur, """
        SELECT source,
               COUNT(DISTINCT committee)::int                                AS committees,
               COUNT(*) FILTER (WHERE committee IS NULL OR committee = '')::int AS no_committee,
               COUNT(*) FILTER (WHERE committee ~ '^C[0-9]{8}$')::int         AS bare_cmte_id,
               COUNT(*)::int                                                  AS gifts
        FROM donations WHERE confirmed GROUP BY 1 ORDER BY 1
    """)
    table(rows, ["source", "committees", "no_committee", "bare_cmte_id", "gifts"])
    print("\n    bare_cmte_id counts rows where the committee NAME was never resolved")
    print("    and the raw FEC id was stored instead (fetch_fec_bulk.py's")
    print("    .get(cmte_id, cmte_id) fallback when run with --no-download).")

    conc = q(cur, """
        WITH t AS (SELECT committee, COUNT(*) n FROM donations
                   WHERE confirmed AND committee IS NOT NULL GROUP BY 1),
             r AS (SELECT n, row_number() OVER (ORDER BY n DESC) rk, SUM(n) OVER () tot FROM t)
        SELECT ROUND(100.0*SUM(n) FILTER (WHERE rk <=  50)/MAX(tot), 1)::float AS pct_top50,
               ROUND(100.0*SUM(n) FILTER (WHERE rk <= 200)/MAX(tot), 1)::float AS pct_top200,
               ROUND(100.0*SUM(n) FILTER (WHERE rk <=1000)/MAX(tot), 1)::float AS pct_top1000,
               COUNT(*)::int AS distinct_committees
        FROM r
    """)
    print("\n  concentration (share of confirmed gifts in the top-N committees)")
    table(conc, ["pct_top50", "pct_top200", "pct_top1000", "distinct_committees"])
    out["committees"] = {"by_source": rows, "concentration": conc}


def section_f(cur, out):
    """Confirmed vs possible-only donors -- what a confirmed-only filter costs."""
    print("\n" + "=" * 78)
    print("F. CONFIRMED vs POSSIBLE-ONLY DONORS")
    print("=" * 78)
    rows = q(cur, """
        WITH s AS (SELECT donor_key, bool_or(confirmed) has_conf,
                          SUM(amount) amt, COUNT(*) n
                   FROM donations GROUP BY 1)
        SELECT has_conf,
               COUNT(*)::int                        AS donors,
               SUM(n)::int                          AS gifts,
               ROUND(SUM(amt)::numeric, 0)::float   AS dollars
        FROM s GROUP BY 1 ORDER BY 1 DESC
    """)
    table(rows, ["has_conf", "donors", "gifts", "dollars"])
    poss = next((r for r in rows if r["has_conf"] is False), None)
    conf = next((r for r in rows if r["has_conf"] is True), None)
    if poss and conf:
        tot = poss["donors"] + conf["donors"]
        print(f"\n    Filtering to confirmed drops {poss['donors']:,} of {tot:,} donors "
              f"({100.0*poss['donors']/tot:.1f}%).")
        print("    That is the correct call for TARGETING -- a possible match is a")
        print("    different person at a different address until proven otherwise, and")
        print("    'possible' is name+last-name-block only, with no address agreement.")
        print("    But it is NOT a neutral filter: it drops disproportionately from")
        print("    common surnames and from cities whose name strings disagree between")
        print("    the voter file and FEC. Report it, do not bury it.")
    out["confirmed_split"] = rows


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", type=Path, default=None,
                    help="also write the raw numbers here, for before/after diffing")
    args = ap.parse_args()

    out = {}
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout = %s", (STATEMENT_TIMEOUT,))
            for fn in (section_a, section_b, section_c, section_d, section_e, section_f):
                fn(cur, out)
    finally:
        conn.close()

    if args.json:
        args.json.write_text(json.dumps(out, indent=2, default=str))
        print(f"\n  raw numbers written to {args.json}")
    print("\n" + "=" * 78)
    print("Re-run this after any re-pull and diff the JSON. The delta is the result.")
    print("=" * 78)


if __name__ == "__main__":
    main()
