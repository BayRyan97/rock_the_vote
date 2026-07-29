"""test_config.py — self-checks for PII destination handling.

The repo lives inside a OneDrive-synced tree and is public on GitHub, so any
path that PII is written to has to be checked against where it actually lands,
not against the literal it was spelled with. The bug this pins: a drive-absolute
Windows literal like C:\\data\\x is a SINGLE RELATIVE COMPONENT on POSIX
(PurePosixPath(...).parts == ('C:\\\\data\\\\x',)), so off Windows it resolved
under the cwd — which run_pipeline.sh sets to the repo root.

Run:  python model/test_config.py     (exit 0 = all checks pass)
"""
import os
import sys
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config as C  # noqa: E402

FAILURES = []


def accepts(name, path):
    try:
        got = C.pii_dest(path, "test data")
        print(f"  [OK] {name} -> {got}")
    except SystemExit as e:
        FAILURES.append(f"{name}: refused, {e}")
        print(f"  [FAIL] {name} was refused: {str(e)[:60]}")


def refuses(name, path, mention=()):
    try:
        got = C.pii_dest(path, "test data")
    except SystemExit as e:
        miss = [m for m in mention if m not in str(e)]
        if miss:
            FAILURES.append(f"{name}: message lacks {miss}")
        print(f"  [{'OK' if not miss else 'FAIL'}] {name}: {str(e)[:70]}")
        return
    FAILURES.append(f"{name}: accepted {got}")
    print(f"  [FAIL] {name} was accepted -> {got}")


def ok(name, got, want):
    good = got == want
    if not good:
        FAILURES.append(f"{name}: got {got!r}, want {want!r}")
    print(f"  [{'OK' if good else 'FAIL'}] {name}" + ("" if good else f"  got={got!r}"))


print("test_config")
print(" A. the POSIX relative-component trap this exists to catch")
ok("drive literal is one relative component on POSIX",
   PurePosixPath(r"C:\data\rock_the_vote_scores").parts,
   (r"C:\data\rock_the_vote_scores",))
# On POSIX that component resolves under the cwd; a bare relative path is the
# same shape, so it must be refused whichever platform we run this on.
refuses("relative path resolves into the repo", "C-data-rock_the_vote_scores",
        mention=["inside the repo"])

print(" B. paths inside the repo are refused")
refuses("model/artifacts", "model/artifacts", mention=["inside the repo"])
refuses("the repo root itself", C.ROOT, mention=["inside the repo"])
refuses("cwd", ".", mention=["inside the repo"])
refuses("nested artifact path", C.ARTIFACTS / "scores", mention=["inside the repo"])

print(" C. synced paths outside the repo are refused too")
refuses("a OneDrive path elsewhere",
        Path.home() / "OneDrive" / "Documents" / "elsewhere",
        mention=["OneDrive-synced"])

print(" D. legitimate destinations are accepted")
accepts("the configured cache", C.CACHE)
accepts("the configured scores dir", C.SCORES_DIR)
accepts("another drive", Path(r"D:\exports") if os.name == "nt" else Path("/srv/exports"))
accepts("a home-relative path", "~/rtv-data-test")

print(" E. PII_ROOT wiring")
ok("CACHE sits under PII_ROOT", C.CACHE.parent, C.PII_ROOT)
ok("SCORES_DIR sits under PII_ROOT", C.SCORES_DIR.parent, C.PII_ROOT)
ok("PII_ROOT is absolute", C.PII_ROOT.is_absolute(), True)
ok("PII_ROOT is outside the repo", C.ROOT in C.PII_ROOT.parents or C.PII_ROOT == C.ROOT, False)

print(" F. RTV_PII_ROOT overrides the default")
import importlib  # noqa: E402

prev = os.environ.get("RTV_PII_ROOT")
try:
    override = str(Path.home() / "rtv-override")
    os.environ["RTV_PII_ROOT"] = override
    reloaded = importlib.reload(C)
    ok("PII_ROOT honours the env var", reloaded.PII_ROOT, Path(override))
    ok("CACHE follows it", reloaded.CACHE, Path(override) / "rock_the_vote_cache")
finally:
    if prev is None:
        os.environ.pop("RTV_PII_ROOT", None)
    else:
        os.environ["RTV_PII_ROOT"] = prev
    importlib.reload(C)
ok("default restored after reload", C.PII_ROOT, Path(os.environ.get("RTV_PII_ROOT") or C._DEFAULT_PII_ROOT).expanduser())

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S):")
    for f in FAILURES:
        print(f"  - {f}")
    sys.exit(1)
print("ALL CHECKS PASSED")
