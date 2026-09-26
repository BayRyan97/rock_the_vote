"""test_db.py -- self-checks for database target resolution and the prod guard.

The guard these pin is the only thing standing between `load_dev_env.py` and
the live database. A false negative is not a bad refresh, it is the loss of the
production project -- so the cases below fix the behaviour that matters:
the same project reached by a different port or pooler mode must still read as
production, and an unidentifiable project must fail closed.

Hermetic: load_env_files is stubbed out, so these never read the developer's
real .env.local and behave the same in CI, where none exists.

Run:  python model/test_db.py     (exit 0 = all checks pass)
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import db as D  # noqa: E402

FAILURES = []

PROD_POOL = "postgresql://postgres.prodref1234567:pw@aws-1-us-west-2.pooler.supabase.com:6543/postgres"
PROD_SESSION = "postgresql://postgres.prodref1234567:pw@aws-1-us-west-2.pooler.supabase.com:5432/postgres"
PROD_DIRECT = "postgresql://postgres:pw@db.prodref1234567.supabase.co:5432/postgres"
DEV_POOL = "postgresql://postgres.devref98765432:pw@aws-1-us-west-2.pooler.supabase.com:6543/postgres"

# The real .env files must never influence a test run.
D.load_env_files = lambda: None


def check(name, got, want):
    if got == want:
        print("  [OK] " + name)
    else:
        FAILURES.append(name + ": got " + repr(got) + ", wanted " + repr(want))
        print("  [FAIL] " + name + ": got " + repr(got) + ", wanted " + repr(want))


def with_env(**kw):
    """Replace the target variables wholesale, so nothing leaks between cases."""
    for var in D.TARGET_ENV.values():
        os.environ.pop(var, None)
    os.environ.pop("RTV_DB_TARGET", None)
    for k, v in kw.items():
        if v is not None:
            os.environ[k] = v


def refuses(name, fn):
    try:
        fn()
    except SystemExit:
        print("  [OK] refuses " + name)
        return
    FAILURES.append(name + ": was allowed")
    print("  [FAIL] allowed " + name)


def allows(name, fn):
    try:
        fn()
    except SystemExit as e:
        FAILURES.append(name + ": was refused (" + str(e) + ")")
        print("  [FAIL] refused " + name)
        return
    print("  [OK] allows " + name)


print("-- project_ref(): both Supabase URL shapes")
check("pooler URL", D.project_ref(PROD_POOL), "prodref1234567")
check("direct URL", D.project_ref(PROD_DIRECT), "prodref1234567")
check("session vs transaction pooler agree",
      D.project_ref(PROD_POOL), D.project_ref(PROD_SESSION))
check("a different project differs",
      D.project_ref(DEV_POOL) == D.project_ref(PROD_POOL), False)
check("non-Supabase host", D.project_ref("postgresql://postgres:pw@localhost:5432/postgres"), None)
check("empty string", D.project_ref(""), None)
check("garbage", D.project_ref("not a url at all"), None)

print()
print("-- resolve_target()")
with_env()
check("defaults to prod", D.resolve_target(), "prod")
check("explicit argument", D.resolve_target("dev"), "dev")
with_env(RTV_DB_TARGET="preview")
check("reads RTV_DB_TARGET", D.resolve_target(), "preview")
check("argument beats RTV_DB_TARGET", D.resolve_target("dev"), "dev")
refuses("an unknown target name", lambda: D.resolve_target("staging"))

print()
print("-- assert_not_prod(): the guard on destructive loads")
with_env(DATABASE_URL=PROD_POOL)
refuses("the prod project by its own URL",
        lambda: D.assert_not_prod(PROD_POOL, "load"))
refuses("the prod project via the OTHER pooler port",
        lambda: D.assert_not_prod(PROD_SESSION, "load"))
refuses("the prod project via its DIRECT connection",
        lambda: D.assert_not_prod(PROD_DIRECT, "load"))
refuses("a destination whose project cannot be identified (fails closed)",
        lambda: D.assert_not_prod("postgresql://postgres:pw@localhost:5432/postgres", "load"))
allows("a genuinely separate project", lambda: D.assert_not_prod(DEV_POOL, "load"))

with_env()
allows("any destination when DATABASE_URL is unset (no prod to protect)",
       lambda: D.assert_not_prod(DEV_POOL, "load"))

print()
print("-- target_url(): the remedy message")
with_env(DATABASE_URL=PROD_POOL)
refuses("a target whose variable is unset", lambda: D.target_url("dev"))
with_env(DATABASE_URL=PROD_POOL, DATABASE_URL_DEV=DEV_POOL)
check("returns the resolved pair", D.target_url("dev"), ("dev", DEV_POOL))

print()
if FAILURES:
    print(str(len(FAILURES)) + " FAILURE(S):")
    for f in FAILURES:
        print("  - " + f)
    sys.exit(1)
print("ALL CHECKS PASSED")
