#!/usr/bin/env python3
"""
load_boe_contacts.py — Load Nassau BOE voter file into boe_contacts table,
then match to the people table via name + city + zip.

Two-pass approach for speed:
  Pass 1: Bulk-insert all BOE rows into boe_contacts (chunked, no lookups)
  Pass 2: Single SQL UPDATE to set people.voter_id by joining on name+city+zip

Usage:
    python build/load_boe_contacts.py [--limit N] [--dry-run]

Environment:
    DATABASE_URL  — Postgres DSN (loaded from .env.local or .env)
"""
import argparse
import json
import os
from pathlib import Path

import pandas as pd
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
BOE_FILE = ROOT / "data" / "Nassau_BOE_voters.csv"

load_dotenv(ROOT / ".env.local")
load_dotenv(ROOT / ".env")

DATABASE_URL = os.environ["DATABASE_URL"]

CHUNK_SIZE = 5_000
BATCH_SIZE = 2_000

VOTING_METHOD_COLS = [
    "voting_method_110425",
    *[f"voting_method_{i}" for i in range(2, 21)],
]


def clean(val, upper=False):
    s = str(val).strip()
    if s in ("", "nan"):
        return None
    return s.upper() if upper else s


def parse_bool(val):
    s = str(val).strip().upper()
    if s in ("Y", "YES", "1"):
        return True
    if s in ("N", "NO", "0"):
        return False
    return None


def parse_date(val):
    s = str(val).strip()
    if not s or s == "nan":
        return None
    try:
        from datetime import datetime
        return datetime.strptime(s, "%m/%d/%Y").date().isoformat()
    except ValueError:
        return None


def parse_phone(area, exchange, last4):
    area = str(area).strip()
    if not area or area in ("", "nan", "0"):
        return None
    return f"({area}) {str(exchange).strip()}-{str(last4).strip()}"


def row_to_tuple(row):
    """Convert a BOE CSV row to an INSERT tuple for boe_contacts."""
    voter_id = clean(row.get("text_voter_id"))
    if not voter_id:
        return None

    # Voting methods — store only non-empty as {col_name: code}
    methods = {
        col: str(row.get(col, "")).strip()
        for col in VOTING_METHOD_COLS
        if str(row.get(col, "")).strip()
    }

    return (
        voter_id,                                          # voter_id
        clean(row.get("text_state_voter_id")),             # state_voter_id
        clean(row.get("cde_voter_status")),                # voter_status
        clean(row.get("cde_voter_reason")),                # voter_status_reason
        clean(row.get("cde_source_of_info")),              # reg_source
        clean(row.get("cde_name_title")),                  # name_title
        clean(row.get("text_name_first"), upper=True),     # name_first
        clean(row.get("text_name_middle"), upper=True),    # name_middle
        clean(row.get("text_name_last"), upper=True),      # name_last
        clean(row.get("cde_name_suffix")),                 # name_suffix
        parse_date(row.get("date_of_birth")),              # dob
        clean(row.get("cde_gender")),                      # gender
        clean(row.get("voter_text_email")),                # email
        parse_phone(                                       # phone
            row.get("text_phone_area_code"),
            row.get("text_phone_exchange"),
            row.get("text_phone_last_four"),
        ),
        clean(row.get("text_res_address_nbr")),            # res_house_num
        clean(row.get("cde_street_dir_prefix")),           # res_street_dir_pre
        clean(row.get("text_street_name"), upper=True),    # res_street_name
        clean(row.get("cde_street_type")),                 # res_street_type
        clean(row.get("cde_street_dir_suffix")),           # res_street_dir_suf
        clean(row.get("cde_res_unit_type")),               # res_unit_type
        clean(row.get("text_res_unit_nbr")),               # res_unit_num
        clean(row.get("text_res_city"), upper=True),       # res_city
        clean(row.get("text_res_zip5"), )[:5] if clean(row.get("text_res_zip5")) else None,  # res_zip
        clean(row.get("text_mail_address1")),              # mail_address1
        clean(row.get("text_mail_address2")),              # mail_address2
        clean(row.get("text_mail_city"), upper=True),      # mail_city
        clean(row.get("text_mail_zip5")),                  # mail_zip
        parse_bool(row.get("ind_mail_military")),          # mail_military
        parse_bool(row.get("ind_mail_foreign")),           # mail_foreign
        parse_bool(row.get("perm_absentee_indicator")),    # perm_absentee
        parse_bool(row.get("ind_res_military")),           # res_military
        parse_bool(row.get("ind_election_wkr")),           # election_worker
        parse_date(row.get("date_of_registration")),       # registration_date
        parse_date(row.get("date_registration_change")),   # registration_change
        parse_date(row.get("date_res_addr_change")),       # res_addr_change
        clean(row.get("desc_party")),                      # party_desc
        clean(row.get("precincttaded_text_name")),         # precinct_name
        clean(row.get("location_text_name")),              # poll_place_name
        clean(row.get("location_text_address1")),          # poll_place_address
        clean(row.get("location_text_city"), upper=True),  # poll_place_city
        clean(row.get("location_text_zip5")),              # poll_place_zip
        clean(row.get("district_ct")),                     # district_ct
        clean(row.get("district_ed")),                     # district_ed
        clean(row.get("district_vi")),                     # district_vi
        json.dumps(methods) if methods else None,          # voting_methods
    )


INSERT_SQL = """
INSERT INTO boe_contacts (
  voter_id, state_voter_id,
  voter_status, voter_status_reason, reg_source,
  name_title, name_first, name_middle, name_last, name_suffix,
  dob, gender,
  email, phone,
  res_house_num, res_street_dir_pre, res_street_name, res_street_type,
  res_street_dir_suf, res_unit_type, res_unit_num, res_city, res_zip,
  mail_address1, mail_address2, mail_city, mail_zip,
  mail_military, mail_foreign,
  perm_absentee, res_military, election_worker,
  registration_date, registration_change, res_addr_change,
  party_desc, precinct_name,
  poll_place_name, poll_place_address, poll_place_city, poll_place_zip,
  district_ct, district_ed, district_vi,
  voting_methods
) VALUES (
  %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
  %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
)
ON CONFLICT (voter_id) DO UPDATE SET
  state_voter_id=EXCLUDED.state_voter_id,
  voter_status=EXCLUDED.voter_status, voter_status_reason=EXCLUDED.voter_status_reason,
  reg_source=EXCLUDED.reg_source,
  name_title=EXCLUDED.name_title, name_first=EXCLUDED.name_first,
  name_middle=EXCLUDED.name_middle, name_last=EXCLUDED.name_last,
  name_suffix=EXCLUDED.name_suffix,
  dob=EXCLUDED.dob, gender=EXCLUDED.gender,
  email=EXCLUDED.email, phone=EXCLUDED.phone,
  res_house_num=EXCLUDED.res_house_num, res_street_dir_pre=EXCLUDED.res_street_dir_pre,
  res_street_name=EXCLUDED.res_street_name, res_street_type=EXCLUDED.res_street_type,
  res_street_dir_suf=EXCLUDED.res_street_dir_suf, res_unit_type=EXCLUDED.res_unit_type,
  res_unit_num=EXCLUDED.res_unit_num, res_city=EXCLUDED.res_city, res_zip=EXCLUDED.res_zip,
  mail_address1=EXCLUDED.mail_address1, mail_address2=EXCLUDED.mail_address2,
  mail_city=EXCLUDED.mail_city, mail_zip=EXCLUDED.mail_zip,
  mail_military=EXCLUDED.mail_military, mail_foreign=EXCLUDED.mail_foreign,
  perm_absentee=EXCLUDED.perm_absentee, res_military=EXCLUDED.res_military,
  election_worker=EXCLUDED.election_worker,
  registration_date=EXCLUDED.registration_date,
  registration_change=EXCLUDED.registration_change,
  res_addr_change=EXCLUDED.res_addr_change,
  party_desc=EXCLUDED.party_desc, precinct_name=EXCLUDED.precinct_name,
  poll_place_name=EXCLUDED.poll_place_name, poll_place_address=EXCLUDED.poll_place_address,
  poll_place_city=EXCLUDED.poll_place_city, poll_place_zip=EXCLUDED.poll_place_zip,
  district_ct=EXCLUDED.district_ct, district_ed=EXCLUDED.district_ed,
  district_vi=EXCLUDED.district_vi,
  voting_methods=EXCLUDED.voting_methods,
  updated_at=now()
"""

MATCH_SQL = """
UPDATE people p
SET voter_id = b.voter_id
FROM boe_contacts b
WHERE p.voter_id IS NULL
  AND p.city = b.res_city
  AND p.zip  = b.res_zip
  AND (
    p.name = b.name_first || ' ' || b.name_last
    OR (b.name_middle IS NOT NULL
        AND p.name = b.name_first || ' ' || b.name_middle || ' ' || b.name_last)
  )
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    print("Pass 1: Loading BOE rows into boe_contacts ...")
    total = 0
    batch = []

    for chunk in pd.read_csv(BOE_FILE, dtype=str, chunksize=CHUNK_SIZE,
                              keep_default_na=False, index_col=False):
        for _, row in chunk.iterrows():
            if args.limit and total >= args.limit:
                break
            tup = row_to_tuple(row)
            if tup:
                batch.append(tup)
                total += 1

            if len(batch) >= BATCH_SIZE and not args.dry_run:
                psycopg2.extras.execute_batch(cur, INSERT_SQL, batch)
                conn.commit()
                batch.clear()

            if total % 50_000 == 0 and total > 0:
                print(f"  {total:,} rows loaded ...")

        if args.limit and total >= args.limit:
            break

    if batch and not args.dry_run:
        psycopg2.extras.execute_batch(cur, INSERT_SQL, batch)
        conn.commit()

    print(f"  Done — {total:,} rows inserted into boe_contacts")

    if not args.dry_run:
        print("\nPass 2: Matching boe_contacts → people via name + city + zip ...")
        cur.execute(MATCH_SQL)
        matched = cur.rowcount
        conn.commit()
        print(f"  Done — {matched:,} people matched and voter_id set")
    else:
        print("\n(dry-run: skipping DB writes and people match)")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
