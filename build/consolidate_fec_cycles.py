#!/usr/bin/env python3
"""consolidate_fec_cycles.py — merge the per-cycle NY files into one indexed corpus.

fetch_fec_bulk.py writes one file per cycle (data/fec_ny_{cycle}.csv). That shape
is right for downloading — each cycle is independent and resumable — and wrong
for everything downstream, which wants a single corpus with a cycle column so
the history-window backtest can slice it.

    2018  1,800,648 rows   2017-2018 activity
    2020  ...             2019-2020
    2022  ...             2021-2022
    2024  ...             2023-2024
    2026  ...             2025-2026 (partial: the cycle is still open)

DEDUPLICATION. Cycle files overlap at the edges — an amended filing can appear
in two cycles, and FEC re-reports some transactions. Rows are deduped on
(cmte_id, name, city, zip, date, amount); the EARLIEST cycle wins, so a
contribution is attributed to the cycle it was actually made in rather than the
one that happened to re-report it. Exact same-signature rows within one cycle
are genuinely ambiguous — two real $100 gifts on one day are indistinguishable
from a double-report — so those are kept and counted, never silently collapsed.

ZIP+4. Files filtered before 2026-08-08 hold zip5 only; later ones keep the full
zip. The output preserves whatever each file had — build/matching.py's split_zip
handles both, so the +4 bonus simply does not fire for the older cycles.

Run from repo root:
    python build/consolidate_fec_cycles.py
    python build/consolidate_fec_cycles.py --cycles 2020 2022 2024 2026
"""
import argparse
import csv
import sys
from collections import Counter
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
DEFAULT_CYCLES = [2018, 2020, 2022, 2024, 2026]
OUT_NAME = "fec_ny_individual_2018_2026.csv"

COLUMNS = ["cycle", "cmte_id", "name", "city", "zip", "date", "amount"]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cycles", type=int, nargs="+", default=DEFAULT_CYCLES)
    # The cycle files are gitignored, so they live in whichever working tree
    # actually ran the fetch — not necessarily the one holding this script.
    ap.add_argument("--data-dir", type=Path, default=DATA,
                    help=f"where the fec_ny_*.csv files live (default: {DATA})")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    data_dir = args.data_dir
    if args.out is None:
        args.out = data_dir / OUT_NAME

    # Signatures from cycles ALREADY processed. Deliberately two sets rather
    # than one: a repeat inside a single cycle file is not the same event as the
    # same row reappearing in a later cycle. The first is genuinely ambiguous —
    # two real $100 gifts on one day are indistinguishable from a double-report
    # — so it is kept and counted. The second is a re-report of a contribution
    # already attributed to an earlier cycle, and is dropped.
    #
    # Stored as 64-bit hashes, not tuples: ~16M signatures as Python tuples runs
    # to gigabytes. Collision probability at this scale is ~1e-5, which is far
    # below the noise already in the source.
    prev_seen: set[int] = set()
    per_cycle = Counter()
    dupes = Counter()
    within = Counter()
    written = 0

    with open(args.out, "w", newline="", encoding="utf-8") as fout:
        w = csv.writer(fout)
        w.writerow(COLUMNS)
        # Ascending, so the earliest cycle claims a shared signature.
        for cycle in sorted(args.cycles):
            src = data_dir / f"fec_ny_{cycle}.csv"
            if not src.exists():
                print(f"  SKIP {src.name} — not found")
                continue
            n = 0
            cur_seen: set[int] = set()
            with open(src, newline="", encoding="latin-1") as fin:
                for row in csv.reader(fin):
                    if len(row) < 6:
                        continue
                    cmte_id, name, city, zipc, date, amt = row[:6]
                    sig = hash((cmte_id, name, city, zipc, date, amt))
                    if sig in prev_seen:
                        dupes[cycle] += 1
                        continue
                    if sig in cur_seen:
                        within[cycle] += 1      # counted, but KEPT
                    cur_seen.add(sig)
                    w.writerow([cycle, cmte_id, name, city, zipc, date, amt])
                    n += 1
                    written += 1
            prev_seen |= cur_seen
            per_cycle[cycle] = n
            print(f"  {src.name:<22} kept {n:>9,}   "
                  f"re-reported from an earlier cycle {dupes[cycle]:>7,}   "
                  f"repeats within this cycle (kept) {within[cycle]:>7,}")

    if not written:
        sys.exit("Nothing written — no cycle files found.")

    size_mb = args.out.stat().st_size / 1048576
    print(f"\n  wrote {args.out}")
    print(f"    {written:,} rows, {size_mb:,.0f} MB")
    print(f"    {sum(dupes.values()):,} cross-cycle re-reports dropped")
    print(f"    {sum(within.values()):,} same-cycle repeats KEPT (ambiguous by nature)")
    print("\n  rows per cycle")
    for c in sorted(per_cycle):
        print(f"    {c}  {per_cycle[c]:>10,}")
    print("\n  Gitignored (data/fec_ny_*), and it carries donor names — keep it local.")


if __name__ == "__main__":
    main()
