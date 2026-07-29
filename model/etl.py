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

import config as C
import sources
from features_person import assemble


def election_day(year: int) -> date:
    """First Tuesday after the first Monday of November."""
    first = date(year, 11, 1)
    first_monday = 1 + (7 - first.weekday()) % 7
    return date(year, 11, first_monday + 1)


def report(persons, donors, elections) -> None:
    dup = persons["person_id"].duplicated().sum()
    if dup:
        print(f"  WARNING: {dup} duplicate person_ids; keeping all rows")
    expected = persons["voters_at_address"].groupby(persons["household_row"]).first().sum()
    print(f"  {len(persons):,} persons (voters_at_address sum = {expected:,})")
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
