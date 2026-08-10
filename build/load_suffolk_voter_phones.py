#!/usr/bin/env python3
"""
load_suffolk_voter_phones.py — Bulk upsert a Suffolk voter-phone export into
boe_contacts. The app's existing phone/email lookups (turfs/[turfId],
turfs/roster, households routes) already LEFT JOIN LATERAL onto boe_contacts
by name + zip with no county filter, so once rows land here Suffolk phones
just show up the same way Nassau's do -- no other code changes needed.

Unlike Nassau's raw BOE export (load_boe_contacts.py), this source has no
local county voter id, dob, email, precinct, or voting-methods data — just
NYS voter id, name, registered address, and an appended phone number. Rows
are loaded with only the columns this file actually has; everything else
stays NULL. The NYS voter id (format "NY" + 18 digits) is used for both
voter_id (the table's PK) and state_voter_id, since there's no local id to
put in voter_id — verified against live data that this can't collide with
Nassau's voter_id values, which are always 8-digit local BOE ids.

There's no people.voter_id backfill pass here (load_boe_contacts.py's
equivalent Pass 2): at this table's size (~2M boe_contacts rows) every
variant tried -- the original cross-county query, a Suffolk-scoped hash
join, a NOT EXISTS guard, a LEFT JOIN anti-join -- either mismatched
Suffolk people against Nassau contacts sharing a name+zip (a few zip codes
straddle the county line) or timed out (>10min). Not pursued further since
nothing in the app reads people.voter_id; phone display only ever depends
on the boe_contacts rows this script writes.

Usage:
    python build/load_suffolk_voter_phones.py [--file PATH] [--limit N] [--dry-run]

Environment:
    DATABASE_URL  — Postgres DSN (loaded from .env.local or .env)
"""
import argparse
import os
import re
from pathlib import Path

import pandas as pd
import psycopg2
import psycopg2.extras

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
FILE_GLOB = "suffolk_current_voters_with_nys_id_*.csv"


def _load_dsn():
    """Same convention as voter_source.py / migrate_donations_psycopg2.py."""
    dsn = os.environ.get("SUPABASE_DSN") or os.environ.get("DATABASE_URL")
    if dsn:
        return dsn
    for fname in (".env.local", ".env"):
        env_file = ROOT / fname
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                for key in ("SUPABASE_DSN=", "DATABASE_URL="):
                    if line.startswith(key):
                        return line.split("=", 1)[1].strip()
    raise SystemExit(
        "SUPABASE_DSN (or DATABASE_URL) not set. Export it or put it in .env")


CHUNK_SIZE = 5_000
BATCH_SIZE = 2_000

PHONE_RE = re.compile(r"\+1(\d{3})(\d{3})(\d{4})")


def default_file():
    """Newest file matching the dated export pattern (filenames sort
    correctly since the date is ISO-formatted)."""
    matches = sorted(DATA_DIR.glob(FILE_GLOB))
    if not matches:
        raise SystemExit(f"No file matching {FILE_GLOB} found in {DATA_DIR}")
    return matches[-1]


def clean(val):
    s = str(val).strip()
    return s if s and s != "nan" else None


def format_phone(raw):
    raw = clean(raw)
    if not raw:
        return None
    m = PHONE_RE.fullmatch(raw)
    if not m:
        return None
    return f"({m.group(1)}) {m.group(2)}-{m.group(3)}"


def parse_address(addr):
    """'STREET, CITY, STATE, ZIP+4' -> (res_city, res_zip5)."""
    parts = [p.strip() for p in str(addr).split(",")]
    if len(parts) < 4:
        return None, None
    res_city = parts[1].upper() or None
    res_zip = parts[3][:5] or None
    return res_city, res_zip


def row_to_tuple(row):
    """Convert a CSV row to an upsert tuple for boe_contacts, or None to
    skip a row with no usable voter id."""
    voter_id = clean(row.get("NYS voter id"))
    if not voter_id:
        return None

    res_city, res_zip = parse_address(row.get("voting address (registered)"))

    return (
        voter_id,                                # voter_id
        voter_id,                                # state_voter_id
        clean(row.get("first name")),            # name_first
        clean(row.get("last name")),              # name_last
        res_city,                                 # res_city
        res_zip,                                  # res_zip
        format_phone(row.get("best contact number")),  # phone
        clean(row.get("contact type")),           # phone_type
    )


INSERT_SQL = """
INSERT INTO boe_contacts (
  voter_id, state_voter_id, name_first, name_last, res_city, res_zip,
  phone, phone_type
) VALUES (
  %s, %s, %s, %s, %s, %s, %s, %s
)
ON CONFLICT (voter_id) DO UPDATE SET
  state_voter_id = EXCLUDED.state_voter_id,
  name_first = EXCLUDED.name_first,
  name_last = EXCLUDED.name_last,
  res_city = EXCLUDED.res_city,
  res_zip = EXCLUDED.res_zip,
  phone = EXCLUDED.phone,
  phone_type = EXCLUDED.phone_type,
  updated_at = now()
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", type=Path, default=None,
                         help="CSV to load (default: newest data/suffolk_current_voters_with_nys_id_*.csv)")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    source_file = args.file or default_file()
    print(f"Loading {source_file}")

    conn = psycopg2.connect(_load_dsn())
    cur = conn.cursor()

    print("Loading rows into boe_contacts ...")
    total = 0
    skipped = 0
    batch = []

    for chunk in pd.read_csv(source_file, dtype=str, chunksize=CHUNK_SIZE,
                              keep_default_na=False, index_col=False):
        for _, row in chunk.iterrows():
            if args.limit and total >= args.limit:
                break
            tup = row_to_tuple(row)
            if tup:
                batch.append(tup)
                total += 1
            else:
                skipped += 1

            if len(batch) >= BATCH_SIZE and not args.dry_run:
                psycopg2.extras.execute_batch(cur, INSERT_SQL, batch)
                conn.commit()
                batch.clear()

            if total % 100_000 == 0 and total > 0:
                print(f"  {total:,} rows loaded ...")

        if args.limit and total >= args.limit:
            break

    if batch and not args.dry_run:
        psycopg2.extras.execute_batch(cur, INSERT_SQL, batch)
        conn.commit()

    if args.dry_run:
        print(f"  (dry-run) {total:,} rows would be upserted into boe_contacts ({skipped:,} skipped, no voter id)")
    else:
        print(f"  Done — {total:,} rows upserted into boe_contacts ({skipped:,} skipped, no voter id)")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
