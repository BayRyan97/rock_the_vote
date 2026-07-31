#!/usr/bin/env python3
"""refresh_cache.py - pull the model tables from Supabase to local Parquet.

This is the ONLY thing that reads the database. etl.py reads the cache this
writes; keeping the pull in one place means the slow, timeout-prone path is
exercised deliberately rather than on every pipeline run.

READ-ONLY on the DB. Writes to config.CACHE (outside OneDrive, outside any git
repo) so nothing syncs to the cloud or risks being committed.

boe_contacts is cached with MODELING COLUMNS ONLY - email, phone, names and
street/mailing address are deliberately NOT selected.

Note on timeouts: Supabase caps statements (2 min by default) and the cap is
transaction-scoped, so `SET statement_timeout = 0` must run in the SAME
transaction as the named cursor. Hence autocommit=False with the timeout set
on the connection before the cursor is declared.

Usage:
    python model/refresh_cache.py            # skips tables already complete
    python model/refresh_cache.py --force    # re-dump everything
"""
import argparse
import json
import sys
import time
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import psycopg2.extras
import pyarrow as pa
import pyarrow.parquet as pq


sys.path.insert(0, str(Path(__file__).resolve().parent))
import config as C  # noqa: E402
from db import connect  # noqa: E402

DEST = C.pii_dest(C.CACHE, "the Supabase cache")
DEST.mkdir(parents=True, exist_ok=True)

CHUNK = 100_000

# boe_contacts: modeling columns only. Excluded on purpose:
#   email, phone, name_title/first/middle/last/suffix, full_name,
#   res_house_num, res_street_*, res_unit_*, mail_*, poll_place_*
BOE_COLS = [
    "voter_id", "state_voter_id", "voter_status", "voter_status_reason",
    "reg_source", "dob", "gender", "party_desc", "registration_date",
    "registration_change", "res_addr_change", "res_city", "res_zip",
    "perm_absentee", "res_military", "election_worker",
    "precinct_name", "district_ct", "district_ed", "district_vi",
    "voting_methods", "updated_at",
]

TABLES = {
    "households":         None,   # None = all columns
    "people":             None,
    "donations":          None,
    "donation_summaries": None,
    "donations_meta":     None,
    "election_results":   None,
    "ev_scores":          None,
    "boe_contacts":       BOE_COLS,
}

PG_TO_PA = {
    "smallint": pa.int16(), "integer": pa.int32(), "bigint": pa.int64(),
    "numeric": pa.float64(), "double precision": pa.float64(), "real": pa.float64(),
    "boolean": pa.bool_(),
    "date": pa.date32(),
    "timestamp with time zone": pa.timestamp("us", tz="UTC"),
    "timestamp without time zone": pa.timestamp("us"),
}


def pa_type(pg_type):
    return PG_TO_PA.get(pg_type, pa.string())   # text/uuid/jsonb/char -> string


def coerce(v, pg_type):
    if v is None:
        return None
    if pg_type in ("jsonb", "json"):
        return json.dumps(v, separators=(",", ":"))
    if isinstance(v, UUID):
        return str(v)
    if isinstance(v, Decimal):
        return float(v)
    if pg_type not in PG_TO_PA and not isinstance(v, str):
        return str(v)
    return v


def dump_to_parquet(fetch, cols, schema, out: Path) -> int:
    """Stream batches into `out` atomically. Returns rows written.

    Writes <out>.partial and renames on success. The cache is the only local
    copy and this pull is slow and timeout-prone, so opening `out` directly --
    which truncates it before a single row lands -- turns a network blip into
    data loss. Measured on an interrupted dump: a Python-level failure leaves a
    VALID file with fewer rows (pyarrow's finalizer writes a footer), and a
    killed process leaves one with no footer that pd.read_parquet rejects
    outright. Both destroy the good copy.

    `fetch` returns the next batch of rows, or an empty sequence when done.
    """
    tmp = out.with_name(out.name + ".partial")
    total, t0 = 0, time.time()
    try:
        writer = pq.ParquetWriter(tmp, schema, compression="zstd")
        try:
            while True:
                rows = fetch()
                if not rows:
                    break
                arrays = [pa.array([coerce(r[i], t) for r in rows], type=pa_type(t))
                          for i, (n, t) in enumerate(cols)]
                writer.write_table(pa.Table.from_arrays(arrays, schema=schema))
                total += len(rows)
                print(f"    {total:>10,} rows  ({time.time() - t0:5.1f}s)")
                sys.stdout.flush()
        finally:
            writer.close()          # a footer makes even a partial diagnosable
    except BaseException:
        tmp.unlink(missing_ok=True)  # never leave a partial masquerading as data
        raise
    tmp.replace(out)                 # atomic on the same volume
    return total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true",
                    help="re-dump tables even if a complete file exists")
    args = ap.parse_args()

    meta = connect(readonly=True, autocommit=True)
    mcur = meta.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    # exact count(*) on multi-million-row tables also exceeds the default
    # Supabase statement_timeout - disable it on the metadata session too.
    mcur.execute("SET statement_timeout = 0")

    summary = []
    try:
        for table, wanted in TABLES.items():
            mcur.execute("""
                SELECT column_name, data_type
                FROM information_schema.columns
                WHERE table_schema='public' AND table_name=%s
                ORDER BY ordinal_position
            """, (table,))
            allcols = [(r["column_name"], r["data_type"]) for r in mcur.fetchall()]
            if not allcols:
                print(f"  !! {table}: not found, skipping")
                continue
            cols = [c for c in allcols if wanted is None or c[0] in wanted]
            names = [c[0] for c in cols]
            schema = pa.schema([pa.field(n, pa_type(t)) for n, t in cols])

            skipped = [c[0] for c in allcols if c[0] not in names]
            out = DEST / f"{table}.parquet"
            collist = ", ".join(f'"{n}"' for n in names)

            print(f"\n{table}")
            print(f"  columns: {len(names)}" + (f"   EXCLUDED (PII): {', '.join(skipped)}" if skipped else ""))
            sys.stdout.flush()

            # resume: skip if a complete file already exists. count(*) is a full
            # scan on multi-million-row tables (see the note above about the
            # statement timeout), so only pay for it when there is a cached file
            # whose completeness has to be judged -- not on --force.
            if out.exists() and not args.force:
                mcur.execute(f'SELECT count(*) AS n FROM public."{table}"')
                db_rows = mcur.fetchone()["n"]
                try:
                    have = pq.ParquetFile(out).metadata.num_rows
                except Exception as e:
                    have = None
                    print(f"  !! existing {out.name} is unreadable "
                          f"({type(e).__name__}) - re-dumping")
                if have == db_rows:
                    mb = out.stat().st_size / 1e6
                    print(f"  -> already cached ({have:,} rows, {mb:.1f} MB) - skipping")
                    summary.append((table, have, mb, skipped))
                    continue
                if have is not None:
                    print(f"  incomplete ({have:,}/{db_rows:,}) - re-dumping")

            # server-side (named) cursors require an open transaction -> autocommit=False
            t0 = time.time()
            # Named cursors require an open transaction -> autocommit=False,
            # which is connect()'s default.
            conn = connect(readonly=True)
            try:
                # Supabase enforces a default statement_timeout; a single long cursor
                # scan blows through it. Disable for this read-only dump session.
                with conn.cursor() as c0:
                    c0.execute("SET statement_timeout = 0")
                cur = conn.cursor(f"dump_{table}")
                cur.itersize = CHUNK
                cur.execute(f'SELECT {collist} FROM public."{table}"')
                total = dump_to_parquet(lambda: cur.fetchmany(CHUNK), cols, schema, out)
                cur.close()
            finally:
                conn.rollback()   # read-only txn; nothing to commit
                conn.close()

            mb = out.stat().st_size / 1e6
            print(f"  -> {out.name}  {total:,} rows  {mb:.1f} MB  in {time.time()-t0:.1f}s")
            summary.append((table, total, mb, skipped))

    finally:
        meta.close()

    print("\n" + "=" * 74)
    print("CACHE SUMMARY")
    print("=" * 74)
    tot_mb = 0
    for t, n, mb, sk in summary:
        tot_mb += mb
        note = f"  (PII cols excluded: {len(sk)})" if sk else ""
        print(f"  {t:<22}{n:>12,} rows{mb:>9.1f} MB{note}")
    print(f"  {'TOTAL':<22}{'':>12}{tot_mb:>14.1f} MB")
    print(f"\n  location: {DEST}")


if __name__ == "__main__":
    main()
