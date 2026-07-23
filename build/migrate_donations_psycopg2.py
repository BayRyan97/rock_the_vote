"""
migrate_donations_psycopg2.py — bulk-insert all FEC + NYBOE donations via psycopg2.

Bypasses PostgREST/HTTP entirely, so no HTTP/2 stream-limit issues.
Run from repo root:
    python3 build/migrate_donations_psycopg2.py

Requires: psycopg2-binary, data/fec_cache.json, data/nyboe_cache.json
"""

import json
import os
import sys
from pathlib import Path

import psycopg2
from psycopg2.extras import execute_values

DSN = os.environ.get(
    "SUPABASE_DSN",
    "postgresql://postgres.sqpjghpvgmahbodlkffl:ugSfCdhhtDEXP65k@aws-1-us-west-2.pooler.supabase.com:5432/postgres",
)
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
            ))
        for item in entry.get("possible", []):
            possible_rows.append((
                donor_key.upper(),
                source,
                _safe_str(item.get("contribution_receipt_date") or item.get("date")),
                _safe_float(item.get("contribution_receipt_amount") or item.get("amount")),
                _safe_str(item.get("committee") or item.get("committee_name") or item.get("filer_name")),
                False,
            ))
    print(f"  {len(confirmed_rows):,} confirmed + {len(possible_rows):,} possible rows from {source}")
    return confirmed_rows + possible_rows


def bulk_insert(cur, rows, source):
    inserted = 0
    for i in range(0, len(rows), BATCH):
        batch = rows[i:i + BATCH]
        execute_values(
            cur,
            """
            INSERT INTO donations (donor_key, source, donation_date, amount, committee, confirmed)
            VALUES %s
            ON CONFLICT DO NOTHING
            """,
            batch,
            template="(%s, %s, %s::date, %s, %s, %s)",
        )
        inserted += len(batch)
        if inserted % 100000 == 0 or inserted == len(rows):
            print(f"  {source}: {inserted:,} / {len(rows):,} inserted...")
    return inserted


def main():
    sources = [
        (DATA / "fec_cache.json", "fec"),
        (DATA / "nyboe_cache.json", "nyboe"),
    ]

    print("Connecting...")
    conn = psycopg2.connect(DSN)
    conn.autocommit = False
    cur = conn.cursor()

    print("Truncating donations table...")
    cur.execute("TRUNCATE TABLE donations")
    conn.commit()

    total = 0
    for path, source in sources:
        if not path.exists():
            print(f"  {path.name} not found, skipping")
            continue
        rows = load_cache(path, source)
        n = bulk_insert(cur, rows, source)
        conn.commit()
        print(f"  committed {n:,} {source} rows")
        total += n

    cur.execute("SELECT COUNT(*) FROM donations;")
    count = cur.fetchone()[0]
    print(f"\nDone. {total:,} rows inserted. DB total: {count:,}")
    conn.close()


if __name__ == "__main__":
    main()
