"""etl.py — Stage A: build the flat person-level table.

Orchestration only. A reader in sources.py produces the raw frames, and
features_person.assemble() derives everything from them, so both sources go
through one definition of every feature.

    <config.ARTIFACTS>/persons.parquet       one row per voter
    <config.ARTIFACTS>/donor_committees.parquet (person_row, committee, source, amount)
    <config.ARTIFACTS>/elections.parquet     (person_row, year, etype, method)
                                             one row per ballot cast

Usage:
    python model/etl.py                                  # from the local cache
    python model/etl.py --source csv                     # from *_Unrolled.csv
    python model/etl.py --county NASSAU --city "GLEN COVE"   # smoke-test subset

To refresh the cache from Supabase: python model/refresh_cache.py
"""
import argparse
from datetime import date
from pathlib import Path

import pandas as pd

import config as C
import sources
from features_person import assemble


def election_day(year: int) -> date:
    """First Tuesday after the first Monday of November."""
    first = date(year, 11, 1)
    first_monday = 1 + (7 - first.weekday()) % 7
    return date(year, 11, first_monday + 1)


def report(persons, donors, elections) -> None:
    # person_id is np.arange, so it can never repeat. person_uuid is the real
    # identity: Supabase dedups same-name-same-household, the CSV path does not.
    dup = int(persons["person_uuid"].duplicated().sum())
    if dup:
        print(f"  WARNING: {dup:,} duplicate person_uuids — household size, both "
              f"leave-self-out denominators and ed_n_voters double-count them")
    # voters_at_address is DERIVED from the parse, so comparing it to the parse
    # proves nothing. declared_voters is the source's own count.
    per_hh = persons.groupby("household_row")[["voters_at_address",
                                               "declared_voters"]].first()
    declared = pd.to_numeric(per_hh["declared_voters"], errors="coerce")
    known = declared.notna()
    if known.any():
        gap = known & (per_hh["voters_at_address"] != declared)
        net = int((declared - per_hh["voters_at_address"])[gap].sum()) if gap.any() else 0
        print(f"  {len(persons):,} persons ({int(declared[known].sum()):,} declared "
              f"by the source across {int(known.sum()):,} households)")
        if gap.any():
            print(f"  WARNING: {int(gap.sum()):,} households parsed a different "
                  f"count than the source declares ({net:+,} people net) — "
                  f"parse_household may be dropping members")
    else:
        print(f"  {len(persons):,} persons (source declares no per-household count)")
    print(f"  party counts:\n{persons['party'].value_counts().head(10).to_string()}")

    elig = persons["y_turnout"] >= 0
    print(f"  y_turnout (voted {C.TARGET_GENERAL_YEAR} general): "
          f"mean {persons.loc[elig, 'y_turnout'].mean():.3f} over {int(elig.sum()):,} "
          f"eligible ({int((~elig).sum()):,} under-18-at-election masked)")
    print(f"  y_party dist: {persons['y_party'].value_counts(normalize=True).round(3).to_dict()}")
    print(f"  donors with committees: {donors['person_row'].nunique():,} "
          f"({donors.shape[0]:,} donation records)")
    if len(elections):
        voters = elections["person_row"].nunique()
        print(f"  election history: {len(elections):,} ballots across {voters:,} voters "
              f"({100 * voters / len(persons):.1f}% of persons; "
              f"years {elections['year'].min()}-{elections['year'].max()})")
    else:
        print("  WARNING: no election history parsed")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=tuple(sources.READERS), default="cache",
                    help="where voter data comes from (default: cache — the "
                         "local Parquet snapshot; refresh it with refresh_cache.py)")
    ap.add_argument("--county", help="restrict to one county (smoke test)")
    ap.add_argument("--city", help="restrict to one city (smoke test)")
    ap.add_argument("--out", type=Path, default=C.PERSONS_PARQUET)
    ap.add_argument("--donors-out", type=Path, default=C.DONOR_COMMITTEES_PARQUET)
    ap.add_argument("--elections-out", type=Path, default=C.ELECTIONS_PARQUET)
    args = ap.parse_args()

    C.ARTIFACTS.mkdir(parents=True, exist_ok=True)
    cutoff = election_day(C.TARGET_GENERAL_YEAR)
    print(f"Loading from {args.source} (donation cutoff {cutoff})...")

    hh, ppl, ballots, don = sources.READERS[args.source](args.county, args.city, cutoff)
    persons, donors, elections = assemble(hh, ppl, ballots, don, cutoff)
    report(persons, donors, elections)

    persons.to_parquet(args.out, index=False)
    donors.to_parquet(args.donors_out, index=False)
    elections.to_parquet(args.elections_out, index=False)
    print(f"Wrote {args.out}, {args.donors_out} and {args.elections_out}")


if __name__ == "__main__":
    main()
