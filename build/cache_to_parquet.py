#!/usr/bin/env python3
"""cache_to_parquet.py — turn the match caches into a local donations frame.

The model layer reads C:\\data\\rock_the_vote_cache\\donations.parquet, which is a
snapshot OF SUPABASE. That is fine for scoring against what is deployed, and
useless for validating a change to the matcher: you would have to write to
production first and inspect afterwards, which is exactly backwards.

This produces the same frame shape straight from data/fec_cache.json and
data/nyboe_cache.json and data/nyccfb_cache.json, so committees.py,
donor_value.py and audit_donations.py
can all run against a fresh match with nothing pointed at the database.

Column-for-column identical to what migrate_donations_psycopg2.py would INSERT,
plus match_score / match_reasons (migration 022). `id` and `created_at` are the
two the database generates; they are synthesised here so the frame is a drop-in
for the cached one.

Run from repo root:
    python build/cache_to_parquet.py
    python build/cache_to_parquet.py --out /abs/path/outside/the/repo.parquet

The default output goes under RTV_PII_ROOT (drive C: data dir on Windows,
~/rtv-data otherwise). Writing inside the repo is refused: see pii_out() below.
"""
import argparse
import json
import os
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import matching  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

# The frame this writes is keyed on donor_key -- NAME|CITY|ZIP straight off the
# BOE voter file -- so its destination is subject to the same rule as every
# other PII artifact: it lives outside the repo, which is public on GitHub.
#
# model/config.py states that rule via pii_dest(), but build/ is a separate
# package with its own requirements and deliberately does not import model/, so
# it is restated here rather than shared. It is *enforced* rather than merely
# documented because the failure is silent and platform-specific: a
# drive-absolute Windows literal like "C:/data/x" is a single RELATIVE
# component on POSIX (PurePosixPath("C:/data/x").parts == ("C:", "data", "x")),
# so off Windows it resolves under the cwd -- and this script's own usage block
# says to run it from the repo root. That is exactly the bug model/config.py's
# docstring describes and model/test_config.py pins.
_DEFAULT_PII_ROOT = Path("C:/data") if os.name == "nt" else Path.home() / "rtv-data"
PII_ROOT = Path(os.environ.get("RTV_PII_ROOT") or _DEFAULT_PII_ROOT).expanduser()
DEFAULT_OUT = PII_ROOT / "rock_the_vote_cache" / "donations_local.parquet"


def pii_out(path: Path) -> Path:
    """Resolve an output path, refusing anything inside the repo."""
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = Path.cwd() / p
    p = p.resolve()
    if p == ROOT or ROOT in p.parents:
        raise SystemExit(
            f"refusing to write donor-identified data inside the repo:\n"
            f"  {p}\n"
            f"This tree is public on GitHub. Pass an absolute --out outside\n"
            f"{ROOT}, or set RTV_PII_ROOT.\n"
            f"(A 'C:/...' path is relative on Linux/macOS and lands under the "
            f"cwd -- that is how this happens.)"
        )
    return p


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def load(path: Path, source: str) -> list[dict]:
    """Flatten one cache file into donation rows.

    Mirrors migrate_donations_psycopg2.load_cache, including its window guard —
    if the two disagreed, a local dry run would be validating something other
    than what the loader will write, which is worse than no dry run at all.
    """
    if not path.exists():
        print(f"  {path.name}: not found — skipping {source}")
        return []
    cache = json.loads(path.read_text())
    rows, skipped = [], 0
    for donor_key, entry in cache.items():
        for bucket, confirmed in (("confirmed", True), ("possible", False)):
            for item in entry.get(bucket, []):
                date = item.get("contribution_receipt_date") or item.get("date") or ""
                if not matching.in_window(str(date)):
                    skipped += 1
                    continue
                rows.append({
                    "donor_key": donor_key.upper(),
                    "source": source,
                    "donation_date": str(date)[:10],
                    "amount": _f(item.get("contribution_receipt_amount") or item.get("amount")),
                    "committee": (item.get("committee") or item.get("committee_name")
                                  or item.get("filer_name")),
                    "confirmed": confirmed,
                    "match_score": item.get("match_score"),
                    "match_reasons": item.get("match_reasons"),
                })
    print(f"  {path.name}: {len(rows):,} rows in window, {skipped:,} outside it")
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fec", type=Path, default=DATA / "fec_cache.json")
    ap.add_argument("--nyboe", type=Path, default=DATA / "nyboe_cache.json")
    ap.add_argument("--nyccfb", type=Path, default=DATA / "nyccfb_cache.json")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()

    print(f"Reading match caches (window {matching.WINDOW_START}..{matching.WINDOW_END})")
    rows = (load(args.fec, "fec") + load(args.nyboe, "nyboe")
            + load(args.nyccfb, "nyccfb"))
    if not rows:
        raise SystemExit("No rows — run the fetchers first.")

    df = pd.DataFrame(rows)
    # Synthesised so the frame is a drop-in for the Supabase snapshot. Deliberately
    # NOT uuid4: a stable, reproducible id makes two local runs diffable.
    df.insert(0, "id", [f"local-{i:09d}" for i in range(len(df))])
    df["created_at"] = pd.Timestamp.utcnow()
    df["match_score"] = pd.to_numeric(df["match_score"], errors="coerce").astype("Int16")

    args.out = pii_out(args.out)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(args.out, index=False)

    conf = df[df.confirmed]
    print(f"\n  wrote {args.out}  ({len(df):,} rows, "
          f"{args.out.stat().st_size/1048576:,.0f} MB)")
    print(f"    confirmed {len(conf):,}   possible {len(df)-len(conf):,}")
    print(f"    donors    {df.donor_key.nunique():,}   confirmed donors {conf.donor_key.nunique():,}")
    print(f"    date range {df.donation_date.min()} .. {df.donation_date.max()}")
    if df.match_score.notna().any():
        print("\n  match_score distribution (confirmed threshold is "
              f"{matching.CONFIRM_THRESHOLD})")
        for lo, hi in [(0, 40), (40, 55), (55, 70), (70, 90), (90, 101)]:
            n = int(((df.match_score >= lo) & (df.match_score < hi)).sum())
            print(f"    {lo:>3}-{hi-1:<3} {n:>10,}")


if __name__ == "__main__":
    main()
