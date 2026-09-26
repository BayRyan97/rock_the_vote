"""db.py — the one way to open a Supabase connection from model/.

Kept out of config.py deliberately: config is imported by every stage including
the GTN path, and none of them should pull in psycopg2 to read a file path.

This is the fifth place the load_dotenv + DATABASE_URL + connect sequence was
written; the other four are refresh_cache.py (now here), score_voters.py (now
here), and three in build/, which is a separate package with its own
requirements and is deliberately left alone.

Targets (task S-04): one codebase, three Supabase projects.
  prod     DATABASE_URL           the live database, ~1.85M people
  dev      DATABASE_URL_DEV       Claude's workspace; a real but SMALL subset
  preview  DATABASE_URL_PREVIEW   EAS preview builds and the canvasser app
`prod` stays the default so every existing caller keeps its current behaviour.
"""
import os
from urllib.parse import urlparse

import psycopg2
from dotenv import load_dotenv

import config as C

# Target -> the environment variable holding its connection string.
TARGET_ENV = {
    "prod": "DATABASE_URL",
    "dev": "DATABASE_URL_DEV",
    "preview": "DATABASE_URL_PREVIEW",
}
DEFAULT_TARGET = "prod"


def load_env_files() -> None:
    """Populate os.environ from the three .env locations, nearest first.

    Repo-root first so a per-checkout .env.local still wins (load_dotenv does
    not override an already-set value), then the durable copy outside the repo.
    The repo one is gitignored and dies with its working tree; config.SECRETS_ENV
    is what survives `git clean -x` and `git worktree remove`.
    """
    load_dotenv(C.ROOT / ".env.local")
    load_dotenv(C.ROOT / ".env")
    load_dotenv(C.SECRETS_ENV)


def project_ref(url: str) -> str | None:
    """The Supabase project reference inside a connection string, or None.

    Two URL shapes are in play and they carry the ref in different places:
      pooler  postgresql://postgres.abcdefghijklmnop:PW@aws-1-...pooler.supabase.com:6543/postgres
      direct  postgresql://postgres:PW@db.abcdefghijklmnop.supabase.co:5432/postgres
    Identifying the project -- rather than comparing whole URLs -- is what lets
    the guard in assert_not_prod() catch the case that actually bites: the same
    project reached through a different port or pooler mode. Whole-string
    equality would call those two different databases and wave the write
    through.
    """
    if not url:
        return None
    try:
        parts = urlparse(url)
    except ValueError:
        return None
    user = parts.username or ""
    if user.startswith("postgres.") and len(user) > len("postgres."):
        return user[len("postgres."):]
    host = (parts.hostname or "").split(".")
    if len(host) > 2 and host[0] == "db" and host[-2:] == ["supabase", "co"]:
        return host[1]
    return None


def resolve_target(target: str | None = None) -> str:
    """Pick the target: explicit argument, else $RTV_DB_TARGET, else prod."""
    chosen = target or os.environ.get("RTV_DB_TARGET") or DEFAULT_TARGET
    if chosen not in TARGET_ENV:
        raise SystemExit(
            f"unknown database target {chosen!r}; expected one of "
            f"{', '.join(sorted(TARGET_ENV))}")
    return chosen


def target_url(target: str | None = None) -> tuple[str, str]:
    """Return (target_name, connection_string), or exit with the remedy."""
    chosen = resolve_target(target)
    var = TARGET_ENV[chosen]
    load_env_files()
    url = os.environ.get(var)
    if not url:
        raise SystemExit(
            f"{var} not set — add it to {C.ROOT / '.env.local'} or to the "
            f"durable copy at {C.SECRETS_ENV} (see .env.local.example)")
    return chosen, url


def assert_not_prod(url: str, what: str) -> None:
    """Refuse a destructive write aimed at the production project.

    Loading a dev environment truncates and repopulates whole tables. Pointed
    at prod that is not a bad refresh, it is the loss of the live database --
    and the mistake is one environment variable wide. The check is on project
    REFERENCE, so prod's pooler URL, its direct URL and its session-mode URL are
    all recognised as the same database.

    Fails closed: if DATABASE_URL is set and neither ref can be parsed, the
    write is refused rather than assumed safe.
    """
    load_env_files()
    prod = os.environ.get(TARGET_ENV["prod"])
    if not prod:
        return
    prod_ref, dest_ref = project_ref(prod), project_ref(url)
    if prod_ref is None or dest_ref is None:
        raise SystemExit(
            f"refusing to {what}: cannot identify the Supabase project behind "
            f"the connection string, so it cannot be shown to differ from "
            f"production. Check the URL shape in .env.local.example.")
    if prod_ref == dest_ref:
        raise SystemExit(
            f"refusing to {what}: that connection points at the PRODUCTION "
            f"project ({prod_ref}). Point DATABASE_URL_DEV at a separate "
            f"Supabase project.")


def connect(readonly: bool = False, autocommit: bool = False,
            target: str | None = None):
    """Open a connection, or exit with the remedy if the URL is unset.

    autocommit defaults to False, matching psycopg2's own default. It matters:
    score_voters.write_scores uses CREATE TEMP TABLE ... ON COMMIT DROP and then
    commits, and refresh_cache's server-side named cursors require an open
    transaction. Only the metadata session in refresh_cache wants autocommit.
    """
    _, url = target_url(target)
    conn = psycopg2.connect(url)
    conn.set_session(readonly=readonly, autocommit=autocommit)
    return conn
