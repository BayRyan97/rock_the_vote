"""db.py — the one way to open a Supabase connection from model/.

Kept out of config.py deliberately: config is imported by every stage including
the GTN path, and none of them should pull in psycopg2 to read a file path.

This is the fifth place the load_dotenv + DATABASE_URL + connect sequence was
written; the other four are refresh_cache.py (now here), score_voters.py (now
here), and three in build/, which is a separate package with its own
requirements and is deliberately left alone.
"""
import os

import psycopg2
from dotenv import load_dotenv

import config as C


def connect(readonly: bool = False, autocommit: bool = False):
    """Open a connection, or exit with the remedy if DATABASE_URL is unset.

    autocommit defaults to False, matching psycopg2's own default. It matters:
    score_voters.write_scores uses CREATE TEMP TABLE ... ON COMMIT DROP and then
    commits, and refresh_cache's server-side named cursors require an open
    transaction. Only the metadata session in refresh_cache wants autocommit.
    """
    # Repo-root first so a per-checkout .env.local still wins (load_dotenv does
    # not override an already-set value), then the durable copy outside the repo.
    # The repo one is gitignored and dies with its working tree; config.SECRETS_ENV
    # is what survives `git clean -x` and `git worktree remove`.
    load_dotenv(C.ROOT / ".env.local")
    load_dotenv(C.ROOT / ".env")
    load_dotenv(C.SECRETS_ENV)
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit(
            f"DATABASE_URL not set — add it to {C.ROOT / '.env.local'} or to the "
            f"durable copy at {C.SECRETS_ENV} (see .env.local.example)")
    conn = psycopg2.connect(url)
    conn.set_session(readonly=readonly, autocommit=autocommit)
    return conn
