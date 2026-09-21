"""
migrate_donations_psycopg2.py — bulk-insert FEC + NYBOE + NYCCFB donations via psycopg2.

Bypasses PostgREST/HTTP entirely, so no HTTP/2 stream-limit issues.
Run from repo root:
    python3 build/migrate_donations_psycopg2.py                  # all sources, full reload
    python3 build/migrate_donations_psycopg2.py --sources nyboe nyccfb  # only these sources

--sources scopes the reload to just the named sources, leaving every other
source's existing rows untouched. Use this when one source's local cache is
far more complete than another's (e.g. a partial fec_cache.json sitting next
to full nyboe/nyccfb ones) and replacing everything would swap a much bigger
existing dataset for a smaller one.

The slow part (loading millions of rows from the JSON caches, over many
network round trips) happens entirely in a scratch `donations_staging`
table, never touching the live `donations` table -- so the site keeps
serving the previous data, unaffected, for the whole load, including
through any retry (see _run_retrying). Only once staging is fully loaded
and deduped does a single short transaction replace `donations`' contents
with staging's (TRUNCATE-or-scoped-DELETE + INSERT ... SELECT, both
server-side, no network round trips) -- so the live table is empty for at
most a few seconds, never however long the load takes, and if that last
step itself fails, Postgres rolls it back and `donations` is untouched.
(Before this, TRUNCATE committed immediately and reload happened in place --
a dropped connection mid-load left the site with an empty donations table
until someone noticed and reran it. That's what happened on 2026-09-20.)

After swapping in, refreshes donations_meta / donation_summaries / the two
donations_by_party materialized views (refresh_aggregates) -- see migration
028 for the one-time cleanup dedupe_cross_source mirrors and why
donor+date+amount is a safe dedup key. Same-source exact repeats are caught
for free by the donations table's partial unique index
(donations_confirmed_dedup_idx, confirmed rows only) -- ON CONFLICT DO
NOTHING below now actually has something to conflict against.

Requires: psycopg2-binary, data/fec_cache.json, data/nyboe_cache.json, data/nyccfb_cache.json
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

import psycopg2
from psycopg2.extras import execute_values

MAX_RETRIES = 5

DSN = os.environ.get("SUPABASE_DSN") or os.environ.get("DATABASE_URL")
if not DSN:
    sys.exit("SUPABASE_DSN (or DATABASE_URL) not set. Export it or put it in .env.local")
DATA = Path(__file__).parent.parent / "data"
BATCH = 5000


def _safe_str(val):
    if not val or not str(val).strip():
        return None
    return str(val).strip()


def _safe_float(val):
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def load_cache(path: Path, source: str):
    print(f"  loading {path.name}...")
    cache = json.loads(path.read_text())
    confirmed_rows = []
    possible_rows = []
    for donor_key, entry in cache.items():
        for item in entry.get("confirmed", []):
            confirmed_rows.append((
                donor_key.upper(),
                source,
                _safe_str(item.get("contribution_receipt_date") or item.get("date")),
                _safe_float(item.get("contribution_receipt_amount") or item.get("amount")),
                # Cache uses "committee" key; fall back to FEC bulk field names
                _safe_str(item.get("committee") or item.get("committee_name") or item.get("filer_name")),
                True,
                _safe_str(item.get("employer")),
                _safe_str(item.get("occupation")),
            ))
        for item in entry.get("possible", []):
            possible_rows.append((
                donor_key.upper(),
                source,
                _safe_str(item.get("contribution_receipt_date") or item.get("date")),
                _safe_float(item.get("contribution_receipt_amount") or item.get("amount")),
                _safe_str(item.get("committee") or item.get("committee_name") or item.get("filer_name")),
                False,
                _safe_str(item.get("employer")),
                _safe_str(item.get("occupation")),
            ))
    print(f"  {len(confirmed_rows):,} confirmed + {len(possible_rows):,} possible rows from {source}")
    return confirmed_rows + possible_rows


def bulk_insert(cur, rows, source, table="donations"):
    inserted = 0
    for i in range(0, len(rows), BATCH):
        batch = rows[i:i + BATCH]
        execute_values(
            cur,
            f"""
            INSERT INTO {table} (donor_key, source, donation_date, amount, committee, confirmed, employer, occupation)
            VALUES %s
            ON CONFLICT DO NOTHING
            """,
            batch,
            template="(%s, %s, %s::date, %s, %s, %s, %s, %s)",
        )
        inserted += len(batch)
        if inserted % 100000 == 0 or inserted == len(rows):
            print(f"  {source}: {inserted:,} / {len(rows):,} inserted...")
    return inserted


def dedupe_cross_source(cur, table="donations"):
    """Removes cross-source / committee-spelling duplicates that the table's
    unique index can't catch (committee and source text legitimately differ
    between sources for the same real-world gift -- e.g. NYC CFB's
    "Diaz Jr., Ruben" vs NY State BOE's "Nyc Diaz" for one contribution).
    Mirrors migration 028's Pass B, including its carve-out for ActBlue/
    WinRed showing up as their own "committee" alongside the actual
    recipient for the same processed gift -- those are real, separate FEC
    filings and are deliberately left alone. Run after every load, full or
    scoped, since duplication risk exists whenever any one source changes.
    `table` is always one of our own hardcoded names ("donations" or
    "donations_staging"), never user input -- safe to interpolate.
    """
    print("Deduplicating cross-source/committee-text duplicates...")
    cur.execute(f"""
        CREATE TEMP TABLE dedup_plan_b AS
        SELECT
          (array_agg(id ORDER BY
             (source = 'nyboe') DESC,
             (source = 'fec') DESC,
             (employer IS NOT NULL AND occupation IS NOT NULL) DESC,
             created_at ASC,
             id ASC
          ))[1] AS keep_id,
          array_agg(id) AS all_ids,
          (array_agg(employer ORDER BY id) FILTER (WHERE employer IS NOT NULL))[1]     AS best_employer,
          (array_agg(occupation ORDER BY id) FILTER (WHERE occupation IS NOT NULL))[1] AS best_occupation
        FROM {table}
        WHERE confirmed = TRUE
        GROUP BY donor_key, donation_date, amount
        HAVING COUNT(*) > 1
           AND NOT (COUNT(DISTINCT source) = 1 AND COUNT(DISTINCT committee) > 1)
    """)
    cur.execute(f"""
        UPDATE {table} d
        SET employer   = COALESCE(d.employer, p.best_employer),
            occupation = COALESCE(d.occupation, p.best_occupation)
        FROM dedup_plan_b p
        WHERE d.id = p.keep_id
    """)
    cur.execute(f"""
        DELETE FROM {table} d
        USING dedup_plan_b p
        WHERE d.id = ANY(p.all_ids) AND d.id <> p.keep_id
    """)
    print(f"  removed {cur.rowcount:,} cross-source duplicate rows")
    cur.execute("DROP TABLE dedup_plan_b")


def refresh_aggregates(cur):
    """Keeps the donations dashboard's precomputed stats in sync after a load."""
    print("Refreshing cached aggregates...")
    cur.execute("""
        UPDATE donations_meta SET
          confirmed_count  = (SELECT COUNT(*) FROM donations WHERE confirmed = true),
          possible_count   = (SELECT COUNT(*) FROM donations WHERE confirmed = false),
          confirmed_total  = (SELECT COALESCE(SUM(amount),0) FROM donations WHERE confirmed = true),
          confirmed_donors = (SELECT COUNT(DISTINCT donor_key) FROM donations WHERE confirmed = true),
          computed_at = now()
        WHERE id = 1
    """)
    cur.execute("""
        INSERT INTO donation_summaries (donor_key, total_donated, donation_count)
        SELECT donor_key, COALESCE(SUM(amount),0), COUNT(*)
        FROM donations WHERE donor_key IS NOT NULL GROUP BY donor_key
        ON CONFLICT (donor_key) DO UPDATE
          SET total_donated = EXCLUDED.total_donated, donation_count = EXCLUDED.donation_count
    """)
    cur.execute("DELETE FROM donation_summaries s WHERE NOT EXISTS (SELECT 1 FROM donations d WHERE d.donor_key = s.donor_key)")
    cur.execute("REFRESH MATERIALIZED VIEW donations_by_party_mv")
    cur.execute("REFRESH MATERIALIZED VIEW donations_by_party_year_mv")


def _connect():
    # keepalives: this load runs long enough (millions of rows over many
    # execute_values round trips) that an idle NAT/firewall in the path can
    # silently drop the connection between batches -- surfaces as
    # "could not receive data from server: Operation timed out" mid-load.
    conn = psycopg2.connect(
        DSN, keepalives=1, keepalives_idle=30, keepalives_interval=10, keepalives_count=5,
    )
    conn.autocommit = False
    cur = conn.cursor()
    cur.execute("SET statement_timeout = 0")  # dedupe/refresh scan ~1M rows; the pooler's default timeout cuts that off
    return conn, cur


def _run_retrying(step_name, conn, cur, fn, *args):
    """Run one step (a function taking cur as its first arg), committing on
    success. On a dropped connection, reconnect and run the WHOLE step over
    from scratch -- every step here (TRUNCATE/DELETE, a source's full
    insert, dedupe, aggregate refresh) is only committed once it completes
    entirely, so nothing is ever partially applied and a from-scratch retry
    can't create duplicates or skip rows."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            result = fn(cur, *args)
            conn.commit()
            return conn, cur, result
        except psycopg2.OperationalError as e:
            print(f"  {step_name}: connection dropped ({e}); reconnecting (attempt {attempt}/{MAX_RETRIES})...")
            try:
                conn.close()
            except Exception:
                pass
            if attempt == MAX_RETRIES:
                raise
            time.sleep(5 * attempt)
            conn, cur = _connect()
    raise RuntimeError("unreachable")


STAGING = "donations_staging"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sources", nargs="+", choices=["fec", "nyboe", "nyccfb"], default=None,
                     help="only reload these sources, leaving other sources' rows untouched; "
                          "default: all sources")
    args = ap.parse_args()

    all_sources = [
        (DATA / "fec_cache.json", "fec"),
        (DATA / "nyboe_cache.json", "nyboe"),
        (DATA / "nyccfb_cache.json", "nyccfb"),
    ]
    sources = [(p, s) for p, s in all_sources if args.sources is None or s in args.sources]

    print("Connecting...")
    conn, cur = _connect()

    # Leftover from a previous run that crashed before reaching the swap.
    # Safe to drop -- it was never live data, just scratch space.
    print(f"Preparing {STAGING}...")
    conn, cur, _ = _run_retrying("prepare staging", conn, cur, lambda c: (
        c.execute(f"DROP TABLE IF EXISTS {STAGING}"),
        c.execute(f"CREATE TABLE {STAGING} (LIKE donations INCLUDING ALL)"),
    ))

    total = 0
    for path, source in sources:
        if not path.exists():
            print(f"  {path.name} not found, skipping")
            continue
        rows = load_cache(path, source)
        conn, cur, n = _run_retrying(f"insert {source}", conn, cur, bulk_insert, rows, source, STAGING)
        print(f"  committed {n:,} {source} rows to staging")
        total += n

    conn, cur, _ = _run_retrying("dedupe", conn, cur, dedupe_cross_source, STAGING)

    # The only step that touches the live table -- server-side TRUNCATE-or-
    # scoped-DELETE + INSERT...SELECT, no network round trips, so this is
    # seconds, not the minutes-to-hours the load above can take. One
    # transaction: if it fails, Postgres rolls it back and `donations` is
    # exactly as it was before this run, never left empty.
    def _swap(c):
        if args.sources is None:
            c.execute("TRUNCATE donations")
        else:
            c.execute("DELETE FROM donations WHERE source = ANY(%s)", (args.sources,))
        c.execute(f"INSERT INTO donations SELECT * FROM {STAGING}")
        c.execute(f"DROP TABLE {STAGING}")
    print("Swapping staged data into donations...")
    conn, cur, _ = _run_retrying("swap", conn, cur, _swap)

    conn, cur, _ = _run_retrying("refresh aggregates", conn, cur, refresh_aggregates)

    cur.execute("SELECT COUNT(*) FROM donations;")
    count = cur.fetchone()[0]
    print(f"\nDone. {total:,} rows staged. DB total: {count:,}")
    conn.close()


if __name__ == "__main__":
    main()
