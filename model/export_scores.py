#!/usr/bin/env python3
"""export_scores.py — scored voter file from the retrained models.

Produces the same quantities score_voters.py writes back to Supabase
(turnout / dem-lean / rep-lean per voter) but from the manifest-driven
pipeline, with both models side by side and the identity + history context
needed to sanity-check a score by eye.

Output goes OUTSIDE the repo by default: the project tree is inside OneDrive
with active sync, and this file carries names, ages, addresses and party
registration for ~1.85M real people. The destination is config.SCORES_DIR, under
config.PII_ROOT (override with RTV_PII_ROOT); config.pii_dest() refuses any path
inside the repo, including one passed via --out-dir.

Usage:
    python model/export_scores.py                    # full parquet + 50k CSV sample
    python model/export_scores.py --sample 100000    # bigger CSV sample
    python model/export_scores.py --out-dir D:\\wherever
"""
import argparse
from pathlib import Path

import numpy as np
import pandas as pd
import config as C
from catboost_util import print_score_distribution, score_persons
from persons_io import load_gtn_scores, load_persons

DEFAULT_OUT = C.SCORES_DIR

# Identity / geography carried through so a score can be inspected in context.
DETAIL = [
    "person_uuid", "name", "age", "party", "tier_letter", "tier_count",
    "county", "town", "city", "zip_code", "address_number", "street_name",
    "election_district", "congressional_district", "senate_district",
    "assembly_district", "lat", "lon", "household_size",
]
# The features that actually drive turnout, so a low score is explainable.
HISTORY = [
    "hist_n_generals", "hist_n_primaries", "hist_years_since_last_vote",
    "hist_years_since_last_general", "hist_general_rate_8", "hist_streak_current",
    "hist_voted_g1", "hist_voted_g2", "hist_never_voted",
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--sample", type=int, default=50_000,
                    help="rows in the CSV sample (0 to skip)")
    ap.add_argument("--seed", type=int, default=C.SEED)
    ap.add_argument("--no-gtn", dest="gtn", action="store_false",
                    help="skip the GTN columns even if scores.parquet exists")
    args = ap.parse_args()
    # --out-dir is user input and was previously unchecked; validate it too.
    args.out_dir = C.pii_dest(args.out_dir, "the scored voter export")
    args.out_dir.mkdir(parents=True, exist_ok=True)

    print("Loading persons + history...")
    p = load_persons()
    print(f"  {len(p):,} voters")

    print("Scoring with CatBoost (turnout + party)...")
    # Through score_persons, not a second copy: the inline version this replaces
    # skipped assert_no_cutoff_spanning and the "party not in features" check,
    # so the export could ship a leaked turnout score the write-back would have
    # refused.
    s = score_persons(p)

    out = pd.DataFrame(index=p.index)
    for c in DETAIL + HISTORY:
        if c in p.columns:
            out[c] = p[c].to_numpy()
        else:
            print(f"  note: {c} not present, skipped")
    # Named from config, not hardcoded: README says to set TARGET_GENERAL_YEAR
    # = 2026 and rerun, after which a "_2024" column would hold the 2026 label.
    actual = f"y_turnout_actual_{C.TARGET_GENERAL_YEAR}"
    out[actual] = p["y_turnout"].to_numpy()

    out["cb_turnout_prob"] = s["turnout"].to_numpy()
    out["cb_dem_lean_prob"] = s["dem_lean"].to_numpy()
    out["cb_rep_lean_prob"] = s["rep_lean"].to_numpy()
    out["cb_other_prob"] = s["other"].to_numpy()

    # The GTN half is optional: `run_pipeline.sh --to baseline` is a documented
    # mode and does not produce scores.parquet. Failing here would throw away
    # the two full predict_proba passes above. A stale-but-present file still
    # raises — that is load_gtn_scores' fingerprint guard, and quietly dropping
    # columns because a file is stale would be the wrong kind of silence.
    if args.gtn and C.SCORES_PARQUET.exists():
        gtn = load_gtn_scores(p)
        out["gtn_turnout_prob"] = gtn["turnout_propensity"].to_numpy(np.float32)
        out["gtn_dem_lean_prob"] = gtn["p_dem_lean"].to_numpy(np.float32)
        out["gtn_rep_lean_prob"] = gtn["p_rep_lean"].to_numpy(np.float32)
        out["gtn_other_prob"] = gtn["p_other"].to_numpy(np.float32)
        out["split"] = gtn["split"].to_numpy()
    elif args.gtn:
        print(f"  note: {C.SCORES_PARQUET.name} not found — CatBoost columns "
              f"only. Run model/evaluate.py for the GTN half.")

    # Provenance flags a consumer needs to read these correctly.
    out["held_out_of_turnout_fit"] = (p["hist_never_voted"] == 1).astype(np.int8)
    out["party_label_masked"] = (p["y_party"] < 0).astype(np.int8)

    full = args.out_dir / "voter_scores_full.parquet"
    out.to_parquet(full, index=False)
    print(f"\nWrote {full}  ({len(out):,} rows x {out.shape[1]} cols, "
          f"{full.stat().st_size / 1e6:.0f} MB)")

    if args.sample:
        n = min(args.sample, len(out))
        # Stratify by registered party so minor parties and BLK are visible in
        # a sample rather than rounded away.
        # max(1, ...) per group plus rounding can land slightly over --sample;
        # the filename below reports the true count, so that is honest not wrong.
        parts = []
        for _, g in out.groupby("party", observed=True):
            parts.append(g.sample(max(1, int(round(n * len(g) / len(out)))),
                                  random_state=args.seed))
        samp = pd.concat(parts).sample(frac=1.0, random_state=args.seed)
        csv = args.out_dir / f"voter_scores_sample_{len(samp)}.csv"
        samp.to_csv(csv, index=False)
        print(f"Wrote {csv}  ({len(samp):,} rows, "
              f"{csv.stat().st_size / 1e6:.1f} MB, stratified by party)")

    print_score_distribution(
        out, [c for c in ("cb_turnout_prob", "gtn_turnout_prob",
                          "cb_dem_lean_prob", "gtn_dem_lean_prob")
              if c in out.columns], width=20)

    print("\n-- turnout by cohort (CatBoost) " + "-" * 22)
    nv = out["held_out_of_turnout_fit"] == 1
    for cohort, m in ((f"held out of fit (no pre-{C.TARGET_GENERAL_YEAR} ballots)", nv),
                      ("everyone else", ~nv)):
        print(f"  {cohort:38s} n={m.sum():>9,}  mean={out.loc[m, 'cb_turnout_prob'].mean():.3f}"
              f"  actual={out.loc[m & (out[actual] >= 0), actual].mean():.3f}")


if __name__ == "__main__":
    main()
