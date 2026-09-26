"""test_ci_guard.py -- self-checks for the CI data-file and secret guards.

A false negative here is a disclosure: the repo is public and the database
behind it covers ~1.85M people. So the blocked-path, secret and file-listing
rules get pinned the same way model/ pins its own invariants.

The secret strings below are synthetic and shaped only to exercise the
regexes -- ci_guard exempts this file from its own scan.

Run:  python scripts/test_ci_guard.py     (exit 0 = all checks pass)
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ci_guard as G  # noqa: E402

GUARD = Path(__file__).resolve().parent / "ci_guard.py"
FAILURES = []


def blocks(name, path):
    if G.is_blocked_path(path):
        print("  [OK] blocks " + name + ": " + path)
    else:
        FAILURES.append(name + ": " + path + " was allowed")
        print("  [FAIL] allowed " + name + ": " + path)


def allows(name, path):
    if not G.is_blocked_path(path):
        print("  [OK] allows " + name + ": " + path)
    else:
        FAILURES.append(name + ": " + path + " was blocked")
        print("  [FAIL] blocked " + name + ": " + path)


def detects(name, text, path="some/file.py"):
    hit = G.find_secret(path, text)
    if hit:
        print("  [OK] detects " + name + " (" + hit[0] + ")")
    else:
        FAILURES.append(name + ": not detected")
        print("  [FAIL] missed " + name)


def clean(name, text, path="some/file.py"):
    hit = G.find_secret(path, text)
    if not hit:
        print("  [OK] no false positive on " + name)
    else:
        FAILURES.append(name + ": false positive (" + hit[0] + ")")
        print("  [FAIL] false positive on " + name + ": " + hit[0])


print("-- blocked file types (S-05 acceptance: a planted .env or CSV fails CI)")
blocks("voter CSV", "data/Nassau.csv")
blocks("CSV anywhere, not just data/", "scripts/scratch/export.csv")
blocks("parquet", "reports/model_scores.parquet")
blocks("xlsx", "notes/LD 16 Super Primes.xlsx")
blocks("sqlite", "apps/canvass-ios/cache.sqlite3")
blocks("base64 blob", "dist/nassau-data.b64")
blocks("bare .env", ".env")
blocks("nested .env", "apps/canvass-ios/.env")
blocks(".env.local", ".env.local")
blocks(".env.production", ".env.production")
blocks("direnv .envrc", ".envrc")
blocks("uppercase extension", "reports/EXPORT.CSV")

print()
print("-- case-insensitivity (the suffix check lowercased but the name regex did not)")
blocks("uppercase .ENV", ".ENV")
blocks("mixed case .Env.local", ".Env.local")
blocks("uppercase nested", "config/.ENV.PRODUCTION")

print()
print("-- compound suffixes (a .gz wrapper must not hide the payload)")
blocks("gzipped CSV", "backup/voters.csv.gz")
blocks("gzipped SQL dump", "backup/dump.sql.gz")
blocks("gzipped JSON", "backup/voters.json.gz")
blocks("tar.gz", "backup/archive.tar.gz")

print()
print("-- data/ is the PII surface: blocked by default, allowlist is the exception")
blocks("new JSON in data/", "data/voters_2026.json")
blocks("new txt in data/", "data/notes.txt")
blocks("new SQL dump in data/", "data/snapshot.sql")
blocks("nested file in data/", "data/exports/2026/people.json")
allows("allowlisted census parquet", "data/tiger_tracts_nassau_suffolk.parquet")
allows("allowlisted TIGER zip", "data/tl_2025_36059_addrfeat.zip")
allows("allowlisted boundary geojson", "data/li_senate_districts.geojson")
allows("allowlisted shapefile sidecar", "data/_tiger_tmp/tl_2023_36_tract.shp.ea.iso.xml")

print()
print("-- donation data: committee-level is allowlisted, donor-level never is")
allows("FEC committee master", "data/fec_committees.csv")
allows("committee tagging v1", "model/donations/committees_tagged.csv")
allows("committee tagging v2", "model/donations/committees_tagged_v2.csv")
allows("committee overrides", "model/donations/committee_overrides_corrected.csv")
allows("aggregate backtest metrics", "model/donations/backtest_results.csv")
# The quarantined class: keyed on "NAME|CITY|ZIP" off the BOE voter file. These
# live outside the repo and must never become allowlisted by association.
blocks("donor match cache (FEC)", "data/fec_cache.json")
blocks("donor match cache (NYBOE)", "data/nyboe_cache.json")
blocks("donor match cache (NYCCFB)", "data/nyccfb_cache.json")
blocks("donor contributions with names", "data/nyccfb_contributions.csv")
blocks("a new donor-level CSV under model/donations", "model/donations/donors_matched.csv")

print()
print("-- allowed outside data/ (a flat blocklist would have broken these)")
allows("the env template", ".env.local.example")
allows("package.json", "package.json")
allows("a migration", "supabase/migrations/042_buildings.sql")
allows("ordinary source", "lib/geoFilters.ts")
allows("a doc", "docs/environment_notes.md")
allows("env-shaped word in a normal name", "docs/environments.md")

print()
print("-- secret shapes")
detects("postgres DSN with inline password",
        'DSN = "postgresql://postgres.abcdefg:hunter2hunter2@aws-1-us-west-2.pooler.supabase.com:5432/postgres"')
detects("postgres:// short scheme", 'postgres://user:p4ssw0rd@db.example.com:5432/x')
detects("service_role JWT",
        'KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyb2xlIjoic2VydmljZV9yb2xlIn0.abcdef123456"')
detects("Anthropic key", 'ANTHROPIC_API_KEY=sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAA')
detects("AWS access key id", 'aws_access_key_id = AKIAIOSFODNN7EXAMPLE')
detects("GitHub token", 'token = ghp_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA')
detects("secret on a later line", "line one\nline two\npostgres://u:p@h/db\n")
detects("DSN split with explicit +",
        'DSN = "postgresql://postgres.abc:secretpw" + "@aws-1.pooler.supabase.com:5432/postgres"')
detects("DSN split across adjacent literals",
        'DSN = ("postgresql://postgres.abc:secretpw"\n       "@aws-1.pooler.supabase.com:5432/postgres")')

print()
print("-- no false positives")
clean("env-var reference", 'const url = process.env.DATABASE_URL;')
clean("password-less DSN", 'postgresql://localhost:5432/postgres')
clean("the documented pooler host", 'DATABASE_POOL_URL points at the transaction-mode pooler (port 6543)')
clean("ordinary TypeScript", 'export function buildGeoWhereSql(f, o) { return { sql: "", params: [] }; }')
clean("a JSON array of strings", '{"parties": ["DEM", "REP", "BLK", "WOR", "CON"]}')
clean("a multi-line JSON array", '{\n  "a": "one",\n  "b": "two",\n  "c": "three"\n}')
clean("its own test file is exempt",
      'postgres://user:hunter2hunter2@host/db', path="scripts/test_ci_guard.py")

print()
print("-- the guard's own source must not trip its own patterns")
clean("ci_guard.py self-scan", GUARD.read_text(encoding="utf-8"), path="scripts/ci_guard.py")

# ---------------------------------------------------------------------------
# Placeholder passwords. .env.local.example exists to show the SHAPE of a DSN,
# so before this exemption the template failed the scan on its own contents and
# no one could edit it without CI rejecting the change. The risk being managed
# is the opposite one -- a real credential hiding behind an example -- so the
# scanner walks every match rather than giving up after the first placeholder.
# ---------------------------------------------------------------------------
print()
print("-- placeholder DSNs in templates")
clean("uppercase PASSWORD placeholder",
      'DATABASE_URL=postgresql://postgres.xxxx:PASSWORD@aws-1.pooler.supabase.com:5432/postgres')
clean("angle-bracket placeholder",
      'DATABASE_URL=postgresql://postgres.ref:<password>@aws-1.pooler.supabase.com:5432/postgres')
clean("the committed template scans clean",
      (Path(__file__).resolve().parent.parent / ".env.local.example").read_text(encoding="utf-8"),
      path=".env.local.example")
detects("a REAL credential after a placeholder one",
        'DATABASE_URL=postgresql://postgres.ref:PASSWORD@host:5432/postgres\n'
        'DATABASE_URL_DEV=postgresql://postgres.dev:hunter2hunter2@host:5432/postgres')
detects("a password that merely contains a placeholder word",
        'postgresql://postgres.ref:password1234beef@host:5432/postgres')


# ---------------------------------------------------------------------------
# changed_files(): the rename hole. --diff-filter=AM silently skipped renames,
# so `git mv notes.md data/voters.csv` -- or a rename that also pasted in a
# credential -- was never examined at all. These build a real throwaway repo.
# ---------------------------------------------------------------------------

def _git(repo, *args):
    subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=T", *args],
        cwd=repo, check=True, capture_output=True,
    )


def guard_exit(repo, base):
    return subprocess.run(
        [sys.executable, str(GUARD), base], cwd=repo, capture_output=True, text=True
    ).returncode


def rename_case(name, setup):
    """setup(repo) -> makes the offending change; we assert the guard exits 1."""
    tmp = Path(tempfile.mkdtemp(prefix="ciguard-"))
    try:
        _git(tmp, "init", "-b", "main")
        (tmp / "notes.md").write_text("harmless notes\n", encoding="utf-8")
        _git(tmp, "add", "-A")
        _git(tmp, "commit", "-m", "base")
        base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp,
                              capture_output=True, text=True, check=True).stdout.strip()
        _git(tmp, "checkout", "-b", "pr")
        setup(tmp)
        _git(tmp, "add", "-A")
        _git(tmp, "commit", "-m", "change")
        code = guard_exit(tmp, base)
        if code == 1:
            print("  [OK] guard blocks " + name)
        else:
            FAILURES.append(name + ": guard exited " + str(code) + ", expected 1")
            print("  [FAIL] guard exited " + str(code) + " for " + name)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _rename_into_data(repo):
    (repo / "data").mkdir(exist_ok=True)
    _git(repo, "mv", "notes.md", "data/voters_2026.csv")


def _rename_plus_credential(repo):
    _git(repo, "mv", "notes.md", "migrate.py")
    p = repo / "migrate.py"
    p.write_text(
        'DSN = "postgresql://postgres.abcdefg:hunter2hunter2@aws-1.pooler.supabase.com:5432/postgres"\n'
        + p.read_text(encoding="utf-8"),
        encoding="utf-8",
    )


def _plain_add(repo):
    (repo / "data").mkdir(exist_ok=True)
    (repo / "data" / "export.csv").write_text("name,zip\n", encoding="utf-8")


print()
print("-- changed_files(): renames must not slip past the guard")
rename_case("a rename INTO a blocked path", _rename_into_data)
rename_case("a rename that also pastes in a credential", _rename_plus_credential)
rename_case("a plain added data file (regression baseline)", _plain_add)

print()
if FAILURES:
    print(str(len(FAILURES)) + " FAILURE(S):")
    for f in FAILURES:
        print("  - " + f)
    sys.exit(1)
print("ALL CHECKS PASSED")
