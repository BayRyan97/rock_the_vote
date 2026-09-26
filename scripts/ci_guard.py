"""Repo guards for CI: no data files, no secrets.

Runs against the files a change actually touches, not the whole history --
history already contains a rotated credential, and re-scanning it would fail
every build forever.

Usage:  python scripts/ci_guard.py <base_ref>
Exit 0 = clean, 1 = blocked.

Tested by scripts/test_ci_guard.py.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import PurePosixPath

# --- what may not enter the repo -------------------------------------------
#
# Two tiers, because one flat list cannot be both safe and usable here:
#
#   1. Suffixes that are never legitimate ANYWHERE. A .csv in scripts/ is as
#      much of a disclosure as one in data/.
#   2. data/ is the PII surface, so inside it the default flips: everything is
#      blocked unless allowlisted. That is what lets .json and .txt stay legal
#      in the rest of the tree (package.json, every README) while a
#      voters.json dropped into data/ is still stopped.
#
# .sql is deliberately NOT globally blocked -- supabase/migrations/ is full of
# legitimate ones. Inside data/, tier 2 catches a .sql dump anyway.
ALWAYS_BLOCKED_SUFFIXES = {
    ".csv", ".tsv", ".parquet", ".xlsx", ".xls", ".xlsm",
    ".db", ".sqlite", ".sqlite3", ".dump", ".b64",
    ".gz", ".bz2", ".7z", ".zip",
}

PII_SURFACE_PREFIXES = ("data/",)

# .env, .env.local, .env.production, .envrc -- any case.
BLOCKED_NAME_RE = re.compile(r"(^|/)\.env(rc)?(\.|$)", re.IGNORECASE)

# Public census geography and the env template: already tracked, no PII, and
# the build depends on them. Anything NOT listed here that matches a rule above
# is blocked. Adding a line is a deliberate act -- say why it carries no PII.
ALLOWLIST = {
    ".env.local.example",
    # TIGER tract shapefile (US Census, public domain).
    "data/_tiger_tmp/tl_2023_36_tract.cpg",
    "data/_tiger_tmp/tl_2023_36_tract.dbf",
    "data/_tiger_tmp/tl_2023_36_tract.prj",
    "data/_tiger_tmp/tl_2023_36_tract.shp",
    "data/_tiger_tmp/tl_2023_36_tract.shp.ea.iso.xml",
    "data/_tiger_tmp/tl_2023_36_tract.shp.iso.xml",
    "data/_tiger_tmp/tl_2023_36_tract.shx",
    # Census/BOE aggregates and boundaries -- tract- or ZIP-level, no individuals.
    "data/acs_nassau_suffolk.json",
    "data/election_results.json",
    "data/ev_zip_counts.json",
    "data/ev_zip_scores.json",
    "data/li_assembly_districts.geojson",
    "data/li_congressional_districts.geojson",
    "data/li_senate_districts.geojson",
    "data/nassau_suffolk_zips.geojson",
    "data/tiger_tracts_nassau_suffolk.parquet",
    # TIGER address-range files for the two counties (US Census, public domain).
    "data/tl_2025_36059_addrfeat.zip",
    "data/tl_2025_36103_addrfeat.zip",
    # Donation-matcher reference data (branch donations-matcher-harvest).
    # COMMITTEE-level, not donor-level: rows are cmte_id / committee name / type /
    # party / classification, with no donor_key, no individual names paired with an
    # address, and no join back to a voter. Checked row-by-row, not just by header.
    # data/fec_committees.csv is the FEC committee master file and carries six
    # treasurer contact emails, which are public record on FEC Form 1.
    #
    # The voter-identified donation files are a DIFFERENT class and stay out of the
    # repo entirely: data/*_cache.json and data/*_contributions.csv are keyed on
    # "NAME|CITY|ZIP" off the BOE voter file. Never allowlist those.
    "data/fec_committees.csv",
    "model/donations/backtest_results.csv",
    "model/donations/committee_overrides_corrected.csv",
    "model/donations/committees_tagged.csv",
    "model/donations/committees_tagged_v2.csv",
}

PG_DSN_LABEL = "postgres connection string with inline password"

# Password segments that are obviously not passwords. Without this the scanner
# cannot tell a template from a leak, and .env.local.example -- whose entire
# job is to show the SHAPE of a DSN -- fails its own guard: every line of it
# reads as a credential, so no one can edit the template without CI rejecting
# the change. Matched on the password segment only, after stripping the
# brackets people write placeholders in. A real Supabase password is generated
# and random; none of these is one.
PLACEHOLDER_PASSWORDS = {
    "password", "pw", "pass", "secret", "your-password", "your_password",
    "yourpassword", "changeme", "example", "redacted", "xxx", "xxxx", "...",
}


def _is_placeholder_password(pw: str) -> bool:
    return pw.strip("<>[]{}()").lower() in PLACEHOLDER_PASSWORDS


SECRET_PATTERNS = [
    (PG_DSN_LABEL,
     re.compile(r"postgres(?:ql)?://[^\s:/@\"']+:(?P<pw>[^\s@\"']+)@", re.I)),
    ("JWT (possible Supabase service_role key)",
     re.compile(r"\beyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{10,}")),
    ("Anthropic API key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}")),
    ("AWS access key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}")),
]

# Source-level string concatenation, so a DSN split across literals still
# matches. Both forms require real evidence of joining -- an explicit `+`, or
# Python's implicit adjacent-literal concatenation across a newline -- so a
# plain array like ["a", "b"] (which has a comma between the quotes) is left
# alone and does not produce false positives.
_JOINS = [
    re.compile(r"[\"']\s*\+\s*[\"']"),
    re.compile(r"[\"']\s*\n\s*[\"']"),
]

# This file's own test necessarily contains literal example secrets.
SCAN_EXEMPT = {"scripts/test_ci_guard.py"}


def _norm(path: str) -> str:
    return path.replace("\\", "/").strip()


def is_blocked_path(path: str) -> bool:
    """True if this path may not enter the repo at all."""
    path = _norm(path)
    if path in ALLOWLIST:
        return False
    if BLOCKED_NAME_RE.search(path):
        return True
    # Every suffix, not just the last: voters.csv.gz and dump.sql.gz must both
    # trip even though .gz is the only trailing one.
    suffixes = {s.lower() for s in PurePosixPath(path).suffixes}
    if suffixes & ALWAYS_BLOCKED_SUFFIXES:
        return True
    low = path.lower()
    return any(low.startswith(p) for p in PII_SURFACE_PREFIXES)


def _scan(text: str):
    """First non-placeholder match as (label, match), else None.

    Every match is walked, not just the first: a file that opens with a
    template DSN and carries a real one further down must still fail. Skipping
    the pattern wholesale after one placeholder is exactly how a leak would
    ride in behind an example.
    """
    for label, pattern in SECRET_PATTERNS:
        for m in pattern.finditer(text):
            if label == PG_DSN_LABEL and _is_placeholder_password(m.group("pw")):
                continue
            return label, m
    return None


def find_secret(path: str, text: str):
    """First secret-shaped match as (label, 1-based line), else None."""
    if _norm(path) in SCAN_EXEMPT:
        return None
    hit = _scan(text)
    if hit:
        label, m = hit
        return label, text[: m.start()].count("\n") + 1
    # Retry against a copy with string-concatenation joins collapsed.
    joined = text
    for rx in _JOINS:
        joined = rx.sub("", joined)
    if joined != text:
        hit = _scan(joined)
        if hit:
            return hit[0] + " (split across string literals)", 0
    return None


def changed_files(base: str):
    """Paths whose CONTENT exists after the change.

    --no-renames matters: with rename detection on (the default since git 2.9)
    a renamed file reports as R, and an R-excluding filter would skip it
    entirely -- so `git mv notes.md data/voters.csv`, or a rename that also
    pastes in a credential, would never be examined. --no-renames decomposes a
    rename into a delete plus an add, so the destination path is checked like
    any other new file. -z keeps non-ASCII paths raw instead of C-quoted.
    """
    out = subprocess.run(
        ["git", "diff", "-z", "--no-renames", "--diff-filter=ACMRT",
         "--name-only", base + "...HEAD"],
        capture_output=True, check=True,
    ).stdout.decode("utf-8", errors="surrogateescape")
    return [f for f in out.split("\0") if f.strip()]


def main() -> int:
    base = sys.argv[1] if len(sys.argv) > 1 else "origin/main"
    files = changed_files(base)
    if not files:
        print("No added/modified files to check.")
        return 0

    print("Checking " + str(len(files)) + " added/modified file(s) against " + base + "...")
    problems = []

    for path in files:
        if is_blocked_path(path):
            problems.append(
                "  BLOCKED FILE  " + path + "\n"
                "                Data/secret file types may not be committed, and everything\n"
                "                under data/ is blocked by default. If this is public reference\n"
                "                geography, add it to ALLOWLIST in scripts/ci_guard.py with a\n"
                "                note on why it carries no PII."
            )

    for path in files:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError as exc:
            # Fail closed: an unreadable file is an unscanned file.
            problems.append(
                "  UNREADABLE    " + path + "\n"
                "                Could not be opened for scanning (" + exc.__class__.__name__ + ").\n"
                "                Treated as a failure rather than skipped."
            )
            continue
        hit = find_secret(path, text)
        if hit:
            label, line = hit
            where = path + (":" + str(line) if line else "")
            problems.append(
                "  SECRET        " + where + "\n"
                "                Looks like a " + label + ". Move it to an env var.\n"
                "                If it is already pushed, ROTATE it -- deleting the line\n"
                "                does not un-publish it from a public repo."
            )

    if problems:
        print("\nBlocked:\n")
        print("\n\n".join(problems))
        print("\n" + str(len(problems)) + " problem(s).")
        return 1

    print("Clean: no data files, no secret-shaped strings.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
