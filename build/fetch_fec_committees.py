#!/usr/bin/env python3
"""fetch_fec_committees.py — download the FEC committee and candidate masters.

These two files carry the answer to "is this committee Democratic, Republican,
or a union PAC", and the pipeline has been downloading half of it and throwing
the answer away: build/fetch_fec_bulk.py's load_committee_names() pulls
cm{yy}.zip on every run and keeps only fields 0 and 1 (CMTE_ID, CMTE_NM). Field
10 is CMTE_PTY_AFFILIATION and field 12 is ORG_TP ('L' = labor).

Cheap: the committee master is ~18k rows and a few MB per cycle, against 5.6 GB
for one cycle of individual contributions. So this is a standalone fetcher
rather than a hook into the bulk path — the committee dimension can be rebuilt
in a couple of minutes without re-downloading any contribution data.

Two files per cycle, because one does not cover the other:

  cm{yy}.zip  committee master. CMTE_PTY_AFFILIATION is populated for party
              committees and many PACs, but is blank for most candidate
              committees and leadership PACs.
  cn{yy}.zip  candidate master. Resolves CAND_ID -> CAND_PTY_AFFILIATION, which
              is how the candidate committees — the bulk of the long tail — get
              a party at all.

This script only EXTRACTS. All classification (side resolution order, labor
tagging, conflict handling across cycles) lives in model/donations/committees.py,
so the raw public record stays separable from our interpretation of it.

Run from repo root:
    python build/fetch_fec_committees.py                                  # 2018-2026
    python build/fetch_fec_committees.py --cycles 2024 2026
    python build/fetch_fec_committees.py --out data/fec_committees.csv
"""
import argparse
import csv
import io
import sys
import zipfile
from pathlib import Path

import requests

DATA = Path(__file__).resolve().parent.parent / "data"
BULK_BASE = "https://www.fec.gov/files/bulk-downloads"
DEFAULT_CYCLES = [2018, 2020, 2022, 2024, 2026]

# Committee master (cm.txt), pipe-delimited, no header.
CM = {
    "cmte_id": 0, "cmte_nm": 1, "cmte_dsgn": 8, "cmte_tp": 9,
    "cmte_pty_affiliation": 10, "org_tp": 12, "connected_org_nm": 13,
    "cand_id": 14,
}
CM_MIN_FIELDS = 15

# Candidate master (cn.txt), pipe-delimited, no header.
CN = {"cand_id": 0, "cand_nm": 1, "cand_pty_affiliation": 2, "cand_office": 5}
CN_MIN_FIELDS = 6

OUT_COLUMNS = [
    "cycle", "cmte_id", "cmte_nm", "cmte_dsgn", "cmte_tp",
    "cmte_pty_affiliation", "org_tp", "connected_org_nm",
    "cand_id", "cand_pty_affiliation", "cand_office",
]


def fetch_pipe_file(url: str) -> list[list[str]]:
    """Download a bulk zip and return its largest member split on '|'.

    latin-1 with errors='replace': the FEC files are not UTF-8 and contain
    stray high bytes in committee names. Decoding strictly would abort the
    whole cycle over a handful of rows.
    """
    resp = requests.get(url, timeout=120)
    resp.raise_for_status()
    rows = []
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        inner = max(zf.infolist(), key=lambda i: i.file_size).filename
        with zf.open(inner) as f:
            for line in io.TextIOWrapper(f, encoding="latin-1", errors="replace"):
                rows.append(line.rstrip("\n").split("|"))
    return rows


def load_cycle(cycle: int) -> list[dict]:
    """One row per committee for this cycle, candidate party already joined."""
    yy = str(cycle)[-2:]

    print(f"  {cycle}: candidate master (cn{yy}.zip)...", end=" ", flush=True)
    candidates = {}
    try:
        for f in fetch_pipe_file(f"{BULK_BASE}/{cycle}/cn{yy}.zip"):
            if len(f) >= CN_MIN_FIELDS:
                candidates[f[CN["cand_id"]].strip()] = (
                    f[CN["cand_pty_affiliation"]].strip(),
                    f[CN["cand_office"]].strip(),
                )
        print(f"{len(candidates):,} candidates", flush=True)
    except Exception as e:
        # Not fatal: committees still get CMTE_PTY_AFFILIATION, just without the
        # candidate fallback. Say so loudly rather than silently degrading.
        print(f"FAILED ({e}) — candidate fallback unavailable for {cycle}", flush=True)

    print(f"  {cycle}: committee master (cm{yy}.zip)...", end=" ", flush=True)
    try:
        raw = fetch_pipe_file(f"{BULK_BASE}/{cycle}/cm{yy}.zip")
    except Exception as e:
        print(f"FAILED ({e}) — skipping cycle", flush=True)
        return []

    out = []
    for f in raw:
        if len(f) < CM_MIN_FIELDS:
            continue
        cand_id = f[CM["cand_id"]].strip()
        cand_pty, cand_office = candidates.get(cand_id, ("", ""))
        out.append({
            "cycle": cycle,
            "cmte_id": f[CM["cmte_id"]].strip(),
            "cmte_nm": f[CM["cmte_nm"]].strip(),
            "cmte_dsgn": f[CM["cmte_dsgn"]].strip(),
            "cmte_tp": f[CM["cmte_tp"]].strip(),
            "cmte_pty_affiliation": f[CM["cmte_pty_affiliation"]].strip(),
            "org_tp": f[CM["org_tp"]].strip(),
            "connected_org_nm": f[CM["connected_org_nm"]].strip(),
            "cand_id": cand_id,
            "cand_pty_affiliation": cand_pty,
            "cand_office": cand_office,
        })
    print(f"{len(out):,} committees", flush=True)
    return out


def report(rows: list[dict]) -> None:
    """What fraction of committees can actually be typed, and by which field.

    This is the number that decides whether the dimension is worth building: if
    neither field resolves a party for most committees, the long tail stays
    unclassified and a curated list is the only option.
    """
    if not rows:
        return
    n = len(rows)
    by_cmte = sum(1 for r in rows if r["cmte_pty_affiliation"])
    by_cand = sum(1 for r in rows if not r["cmte_pty_affiliation"] and r["cand_pty_affiliation"])
    labor = sum(1 for r in rows if r["org_tp"] == "L")
    connected = sum(1 for r in rows if r["connected_org_nm"])

    print(f"\n  committee-cycle rows            {n:>10,}")
    print(f"  distinct committees             {len({r['cmte_id'] for r in rows}):>10,}")
    print(f"  party from CMTE_PTY_AFFILIATION {by_cmte:>10,}  ({100.0*by_cmte/n:.1f}%)")
    print(f"  party added by candidate join   {by_cand:>10,}  ({100.0*by_cand/n:.1f}%)")
    print(f"  -> total with a party           {by_cmte+by_cand:>10,}  "
          f"({100.0*(by_cmte+by_cand)/n:.1f}%)")
    print(f"  ORG_TP = 'L' (labor)            {labor:>10,}  ({100.0*labor/n:.1f}%)")
    print(f"  has CONNECTED_ORG_NM            {connected:>10,}  ({100.0*connected/n:.1f}%)")

    print("\n  CMTE_PTY_AFFILIATION values (top 12)")
    counts = {}
    for r in rows:
        counts[r["cmte_pty_affiliation"] or "(blank)"] = \
            counts.get(r["cmte_pty_affiliation"] or "(blank)", 0) + 1
    for k, v in sorted(counts.items(), key=lambda kv: -kv[1])[:12]:
        print(f"    {k:<12} {v:>8,}")

    print("\n  ORG_TP values (blank = not a connected committee)")
    counts = {}
    for r in rows:
        counts[r["org_tp"] or "(blank)"] = counts.get(r["org_tp"] or "(blank)", 0) + 1
    for k, v in sorted(counts.items(), key=lambda kv: -kv[1])[:10]:
        label = {"L": "L  labor", "C": "C  corporation", "M": "M  membership org",
                 "T": "T  trade association", "V": "V  cooperative",
                 "W": "W  corporation w/o capital stock"}.get(k, k)
        print(f"    {label:<32} {v:>8,}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cycles", type=int, nargs="+", default=DEFAULT_CYCLES,
                    help=f"election cycles (default: {DEFAULT_CYCLES})")
    ap.add_argument("--out", type=Path, default=DATA / "fec_committees.csv",
                    help="output CSV (default: data/fec_committees.csv)")
    args = ap.parse_args()

    print(f"Fetching FEC committee + candidate masters for {args.cycles}")
    rows = []
    for cycle in sorted(args.cycles):
        rows.extend(load_cycle(cycle))

    if not rows:
        sys.exit("No committee data fetched — nothing written.")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=OUT_COLUMNS)
        w.writeheader()
        w.writerows(rows)

    report(rows)
    size_kb = args.out.stat().st_size / 1024
    print(f"\n  wrote {args.out} ({size_kb:,.0f} KB)")
    print("  Public, non-PII, small — check it in so the dimension rebuilds offline.")


if __name__ == "__main__":
    main()
