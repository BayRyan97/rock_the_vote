#!/usr/bin/env python3
"""
voter_source.py — shared voter iterator for the donation-matching fetch
scripts (fetch_fec.py, fetch_fec_bulk.py, fetch_nyboe.py, fetch_nyccfb.py).

Queries the live `people`/`households` tables in Supabase instead of the
gitignored Nassau.csv/Suffolk.csv exports those scripts used to require.
donor_key is already stored as "NAME|CITY|ZIP" -- the exact match key these
scripts used to build by hand from household_detail -- and tier_letter/
tier_count split what household_detail encoded as a single "I0"/"F1"/"L1"
string, so no household_detail parsing is needed here at all.

Requires env var SUPABASE_DSN (falls back to DATABASE_URL), same convention
as migrate_donations_psycopg2.py.
"""
import os
from pathlib import Path

import psycopg2

DROPOFF_TIERS = {"I0", "F1", "L1"}
# (tier_letter, tier_count) pairs, e.g. {"I0","F1","L1"} -> [("I",0),("F",1),("L",1)].
# people has a btree index on (tier_letter, tier_count) but not on the
# concatenated "I0"-style string, so the WHERE clause below compares the two
# raw columns directly rather than reconstructing and matching the string --
# an expression like (tier_letter || tier_count) IN (...) can't use that
# index and forces a sequential scan over ~1.8M rows.
DROPOFF_TIER_PAIRS = [(t[0], int(t[1:])) for t in DROPOFF_TIERS]

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"


def _load_dsn():
    dsn = os.environ.get("SUPABASE_DSN") or os.environ.get("DATABASE_URL")
    if dsn:
        return dsn
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            for key in ("SUPABASE_DSN=", "DATABASE_URL="):
                if line.startswith(key):
                    return line.split("=", 1)[1].strip()
    raise SystemExit(
        "SUPABASE_DSN (or DATABASE_URL) not set. Export it or put it in .env")


def _priority_clause(include_rep, all_parties):
    """SQL equivalent of the old priority_names() household_detail filter:
    BLK, DEM-dropoff (tier I0/F1/L1), plus REP if include_rep, or everyone
    if all_parties. Returns (clause, params) or (None, []) for all_parties."""
    if all_parties:
        return None, []
    tier_ors = " OR ".join(["(p.tier_letter = %s AND p.tier_count = %s)"] * len(DROPOFF_TIER_PAIRS))
    clause = f"(p.party = 'BLK' OR (p.party = 'DEM' AND ({tier_ors})))"
    params = [x for pair in DROPOFF_TIER_PAIRS for x in pair]
    if include_rep:
        clause = f"({clause} OR p.party = 'REP')"
    return clause, params


def _server_cursor(conn):
    """Named (server-side) cursor: rows stream from Postgres as the scan
    progresses instead of the default client-side cursor buffering the
    entire ~1.8M-row-table result before the first row is available --
    which also means an early `break` in a caller actually cuts work short
    instead of waiting for the full result set regardless."""
    cur = conn.cursor(name="voter_source_cursor")
    cur.itersize = 2000
    return cur


def iter_voters(filter_county=None, include_rep=False, all_parties=False):
    """Yield (donor_key, name, city, zip5) for each priority voter.

    Used by fetch_nyboe.py, fetch_nyccfb.py, fetch_fec_bulk.py -- scripts
    that only need the match key, not scoring inputs. Mirrors those scripts'
    original iter_voters(): no address-completeness filter, county join only
    when requested.
    """
    conn = psycopg2.connect(_load_dsn())
    try:
        cur = _server_cursor(conn)
        where = ["p.donor_key IS NOT NULL", "p.city IS NOT NULL", "p.zip IS NOT NULL"]
        params = []

        priority_clause, priority_params = _priority_clause(include_rep, all_parties)
        if priority_clause:
            where.append(priority_clause)
            params.extend(priority_params)

        join = ""
        if filter_county:
            join = "JOIN households h ON h.id = p.household_id"
            where.append("h.county = %s")
            params.append(filter_county.upper())

        sql = f"""
            SELECT p.donor_key, p.name, p.city, p.zip
            FROM people p
            {join}
            WHERE {' AND '.join(where)}
        """
        cur.execute(sql, params)

        seen = set()
        for donor_key, name, city, zip5 in cur:
            if donor_key in seen:
                continue
            seen.add(donor_key)
            yield donor_key, name, city, (zip5 or "").strip()[:5]
    finally:
        conn.close()


def query_voters(filter_county=None, filter_district=None, include_rep=False, all_parties=False):
    """Yield dicts of voter+household fields for FEC-likelihood scoring.

    Used by fetch_fec.py's build_task_list(), which needs party/tier/age
    (not just the match key) to rank the task list. Always joins households:
    fetch_fec.py needs county/assembly_district for its filters, and excludes
    anyone whose household has no street address -- the same dropna the old
    Nassau.csv/Suffolk.csv path applied before household_detail parsing.
    """
    conn = psycopg2.connect(_load_dsn())
    try:
        cur = _server_cursor(conn)
        where = ["p.donor_key IS NOT NULL", "h.address_num IS NOT NULL", "h.street IS NOT NULL"]
        params = []

        priority_clause, priority_params = _priority_clause(include_rep, all_parties)
        if priority_clause:
            where.append(priority_clause)
            params.extend(priority_params)

        if filter_county:
            where.append("h.county = %s")
            params.append(filter_county.upper())
        if filter_district is not None:
            where.append("h.assembly_district = %s")
            params.append(filter_district)

        sql = f"""
            SELECT p.donor_key, p.name, p.party, p.tier_letter, p.tier_count,
                   p.age, p.city, p.zip
            FROM people p
            JOIN households h ON h.id = p.household_id
            WHERE {' AND '.join(where)}
        """
        cur.execute(sql, params)

        seen = set()
        for donor_key, name, party, tier_letter, tier_count, age, city, zip5 in cur:
            if donor_key in seen:
                continue
            seen.add(donor_key)
            tier = f"{tier_letter}{tier_count}" if tier_letter is not None and tier_count is not None else None
            yield {
                "donor_key": donor_key,
                "name": name,
                "party": party,
                "tier": tier,
                "age": age,
                "city": city,
                "zip5": (zip5 or "").strip()[:5],
            }
    finally:
        conn.close()
