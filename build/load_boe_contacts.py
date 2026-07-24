#!/usr/bin/env python3
"""
load_boe_contacts.py — Match Nassau BOE voter file to people table and
populate email, phone, and voter_id columns.

Usage:
    python build/load_boe_contacts.py [--limit N] [--dry-run]

Environment:
    DATABASE_URL  — Postgres DSN (loaded from .env.local or .env)
"""
import argparse
import os
from pathlib import Path

import pandas as pd
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
BOE_FILE = DATA_DIR / "Nassau_BOE_voters.csv"

load_dotenv(ROOT / ".env.local")
load_dotenv(ROOT / ".env")

DATABASE_URL = os.environ["DATABASE_URL"]

CHUNK_SIZE = 10_000
BATCH_SIZE = 500


def parse_phone(area: str, exchange: str, last4: str) -> "str | None":
    area = str(area).strip()
    exchange = str(exchange).strip()
    last4 = str(last4).strip()
    if not area or area in ("nan", "0"):
        return None
    return f"({area}) {exchange}-{last4}"


def parse_name(first: str, middle: str, last: str) -> "tuple[str, str]":
    """Return (short_name, long_name): 'FIRST LAST' and 'FIRST MIDDLE LAST'."""
    first = str(first).strip().upper()
    middle = str(middle).strip().upper()
    last = str(last).strip().upper()
    short = f"{first} {last}"
    long = f"{first} {middle} {last}" if middle and middle != "NAN" else short
    return short, long


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="Max rows to process (for testing)")
    parser.add_argument("--dry-run", action="store_true", help="Match only, do not write to DB")
    args = parser.parse_args()

    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    total_rows = 0
    skipped_no_contact = 0
    matched = 0
    unmatched = 0
    updates = []  # (voter_id, email, phone, person_uuid)

    print(f"Reading {BOE_FILE} ...")

    reader = pd.read_csv(
        BOE_FILE,
        dtype=str,
        chunksize=CHUNK_SIZE,
        keep_default_na=False,
        index_col=False,
    )

    for chunk in reader:
        for _, row in chunk.iterrows():
            if args.limit and total_rows >= args.limit:
                break
            total_rows += 1

            email = str(row.get("voter_text_email", "")).strip() or None
            phone = parse_phone(
                row.get("text_phone_area_code", ""),
                row.get("text_phone_exchange", ""),
                row.get("text_phone_last_four", ""),
            )

            if not email and not phone:
                skipped_no_contact += 1
                continue

            voter_id = str(row.get("text_voter_id", "")).strip() or None
            short_name, long_name = parse_name(
                row.get("text_name_first", ""),
                row.get("text_name_middle", ""),
                row.get("text_name_last", ""),
            )
            city = str(row.get("text_res_city", "")).strip().upper()
            zip5 = str(row.get("text_res_zip5", "")).strip()[:5]

            # Try short name first, then with middle initial
            person_id = None
            for name in dict.fromkeys([short_name, long_name]):
                cur.execute(
                    "SELECT id FROM people WHERE name = %s AND city = %s AND zip = %s LIMIT 1",
                    (name, city, zip5),
                )
                row_result = cur.fetchone()
                if row_result:
                    person_id = row_result[0]
                    break

            if person_id:
                matched += 1
                # (voter_id for people join, voter_id/email/phone for boe_contacts)
                updates.append((voter_id, voter_id, email, phone, person_id))
            else:
                unmatched += 1

            # Flush batch
            if len(updates) >= BATCH_SIZE and not args.dry_run:
                # Write voter_id to people for join key
                psycopg2.extras.execute_batch(
                    cur,
                    "UPDATE people SET voter_id=%s WHERE id=%s",
                    [(u[0], u[4]) for u in updates],
                )
                # Upsert contact info into boe_contacts
                psycopg2.extras.execute_batch(
                    cur,
                    """INSERT INTO boe_contacts (voter_id, email, phone)
                       VALUES (%s, %s, %s)
                       ON CONFLICT (voter_id) DO UPDATE
                         SET email=EXCLUDED.email, phone=EXCLUDED.phone, updated_at=now()""",
                    [(u[1], u[2], u[3]) for u in updates],
                )
                conn.commit()
                updates.clear()

        if args.limit and total_rows >= args.limit:
            break

        if total_rows % 50_000 == 0:
            print(
                f"  {total_rows:,} rows processed | "
                f"matched: {matched:,} | unmatched: {unmatched:,} | "
                f"skipped (no contact): {skipped_no_contact:,}"
            )

    # Final flush
    if updates and not args.dry_run:
        psycopg2.extras.execute_batch(
            cur,
            "UPDATE people SET voter_id=%s WHERE id=%s",
            [(u[0], u[4]) for u in updates],
        )
        psycopg2.extras.execute_batch(
            cur,
            """INSERT INTO boe_contacts (voter_id, email, phone)
               VALUES (%s, %s, %s)
               ON CONFLICT (voter_id) DO UPDATE
                 SET email=EXCLUDED.email, phone=EXCLUDED.phone, updated_at=now()""",
            [(u[1], u[2], u[3]) for u in updates],
        )
        conn.commit()

    cur.close()
    conn.close()

    print(
        f"\nDone. {total_rows:,} rows | "
        f"matched: {matched:,} | unmatched: {unmatched:,} | "
        f"skipped (no contact): {skipped_no_contact:,}"
    )
    if args.dry_run:
        print("(dry-run: no writes made)")


if __name__ == "__main__":
    main()
