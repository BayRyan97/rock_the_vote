#!/usr/bin/env python3
"""write_supabase.py — publish donation artifacts, with the naming contract enforced.

WHY A MODULE EXISTS JUST TO GUARD COLUMN NAMES.

Objective 4 in doc/target_strategies.md asks for donor ROI: how many extra
dollars does asking a given donor produce? That quantity is NOT COMPUTABLE from
anything in this repository, and no amount of modelling changes it. It needs
solicitation records -- who was asked, when, through which channel -- and there
are none. FEC, NY BOE and NYC CFB all record gifts RECEIVED. None of them record
asks MADE, because nobody is required to file them.

What this pipeline can produce is P(gives at all) x E[amount if they give],
which is the expected dollars from a donor UNDER WHATEVER SOLICITATION THEY
ALREADY GET. That is a useful sort. It is not ROI, not uplift, not incremental
lift, and the difference is not pedantry: a donor with a high untreated
expectation may be someone who gives every year regardless, in which case asking
them harder returns nothing. Ranking by this number and calling it ROI would
send the programme at exactly the donors least likely to respond to it.

The gap closes only through migration 024's randomized ask holdout, and only
after enough time has passed to observe both arms. Until then the honest column
name is `expected_dollars_untreated`, and the "untreated" suffix is the whole
point of it.

So: this module refuses to write any column whose name contains "roi", "uplift"
or "lift". A rename is the specific way this claim would get lost -- someone
shortens the column for a dashboard header, and six months later a memo says
"our model ranks donors by ROI". The check is here rather than in review because
review is where it would slip through.

Usage:
    python model/donations/write_supabase.py --dry-run
    python model/donations/write_supabase.py --write
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config as C  # noqa: E402

# Substring, not exact match, and case-insensitive: `donor_roi`, `roi_score`,
# `uplift_pct` and `expected_lift` must all fail. Substring matching is what
# makes the guard hard to route around by accident.
FORBIDDEN_SUBSTRINGS = ("roi", "uplift", "lift")

# ...which means a legitimate name containing one of them by coincidence would
# also be refused. That is the correct trade -- there are very few such words in
# this domain and the cost of a false positive is renaming a column, while the
# cost of a false negative is a causal claim nobody can support. Any exception
# has to be listed here deliberately.
ALLOWED_EXACT: frozenset[str] = frozenset()

TABLES = {
    "donor_lean": ("donor_key", C.ARTIFACTS / "donor_lean.parquet"),
    "donor_propensity": ("donor_key", C.ARTIFACTS / "donor_propensity.parquet"),
}


class NamingContractError(ValueError):
    """Raised when an outbound column implies a causal claim we cannot support."""


def check_columns(columns) -> None:
    """Refuse names implying incrementality. Raises rather than warns.

    A warning would be printed into a log nobody reads while the column shipped
    anyway, which is the same as not having the check.
    """
    bad = []
    for col in columns:
        name = str(col).lower()
        if name in ALLOWED_EXACT:
            continue
        for token in FORBIDDEN_SUBSTRINGS:
            if token in name:
                bad.append((col, token))
                break
    if bad:
        listed = "\n".join(f"    {c!r} contains {t!r}" for c, t in bad)
        raise NamingContractError(
            "these column names claim incrementality this data cannot support:\n"
            f"{listed}\n"
            "  There are no solicitation records in FEC, NY BOE or NYC CFB, so no\n"
            "  ROI, uplift or incremental-lift quantity is computable. What the\n"
            "  pipeline produces is expected dollars under existing solicitation:\n"
            "  name it `expected_dollars_untreated`. To measure the real thing,\n"
            "  apply migration 024 and run the randomized ask holdout."
        )


def check_frame(df: pd.DataFrame, key: str) -> None:
    check_columns(df.columns)
    if key not in df.columns:
        raise ValueError(f"missing key column {key!r}")
    dupes = int(df[key].duplicated().sum())
    if dupes:
        raise ValueError(f"{dupes:,} duplicate {key} values; upsert would be ambiguous")


def _dsn() -> str:
    dsn = os.environ.get("SUPABASE_DSN") or os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit(
            "set SUPABASE_DSN (or DATABASE_URL) to write. --dry-run needs neither.")
    return dsn


def assert_table_exists(cur, table: str) -> None:
    """Fail loudly if the migration has not been applied.

    donations_meta and the nyccfb source constraint both reached production
    without a migration behind them. An auto-create here would make this the
    third, so it refuses instead.
    """
    cur.execute("SELECT to_regclass(%s)", (f"public.{table}",))
    if cur.fetchone()[0] is None:
        raise SystemExit(
            f"table {table!r} does not exist. Apply the migration that creates it "
            f"(supabase/migrations/024_donor_propensity.sql) — this script will "
            f"not create tables, because a schema nobody wrote down is how "
            f"donations_meta ended up undocumented for months.")


def upsert(df: pd.DataFrame, table: str, key: str, *, dsn: str,
           batch: int = 5000) -> int:
    """Upsert, never TRUNCATE.

    refresh_donation_aggregates.py learned this the hard way: TRUNCATE plus a
    slow reload leaves the table empty for the duration, and if the reload
    fails the table stays empty. Upserting keeps the previous scores readable
    until the new ones land.
    """
    import psycopg2
    from psycopg2.extras import execute_values

    cols = list(df.columns)
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c != key)
    sql = (f"INSERT INTO {table} ({', '.join(cols)}) VALUES %s "
           f"ON CONFLICT ({key}) DO UPDATE SET {updates}, refreshed_at = now()")

    conn = psycopg2.connect(dsn)
    conn.autocommit = False
    try:
        cur = conn.cursor()
        assert_table_exists(cur, table)
        rows = list(df.itertuples(index=False, name=None))
        for i in range(0, len(rows), batch):
            execute_values(cur, sql, rows[i:i + batch])
            print(f"    {min(i + batch, len(rows)):,} / {len(rows):,}")
        conn.commit()
    finally:
        conn.close()
    return len(df)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tables", nargs="+", default=list(TABLES), choices=list(TABLES))
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true", help="validate, write nothing")
    g.add_argument("--write", action="store_true")
    args = ap.parse_args()

    dsn = _dsn() if args.write else None
    for table in args.tables:
        key, path = TABLES[table]
        if not path.exists():
            print(f"  {table}: {path.name} not built, skipping")
            continue
        df = pd.read_parquet(path)
        check_frame(df, key)
        print(f"  {table}: {len(df):,} rows, {len(df.columns)} columns — "
              f"naming contract OK")
        if args.write:
            n = upsert(df, table, key, dsn=dsn)
            print(f"  {table}: {n:,} rows upserted")

    if args.dry_run:
        print("\n[dry run] nothing written.")


if __name__ == "__main__":
    main()
