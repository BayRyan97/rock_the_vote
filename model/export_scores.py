#!/usr/bin/env python3
"""export_scores.py — scored voter file from the retrained models.

Produces the same quantities score_voters.py writes back to Supabase
(turnout / dem-lean / rep-lean per voter) but from the manifest-driven
pipeline, with both models side by side and the identity + history context
needed to sanity-check a score by eye.

Output goes OUTSIDE the repo by default: the project tree is inside OneDrive
with active sync, and this file carries names, ages, addresses and party
registration for ~1.85M real people. See C:\\data\\rock_the_vote_cache\\README.txt.

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
from catboost_util import load_model, manifest_features, prepare
from persons_io import load_persons

DEFAULT_OUT = Path(r"C:\data\rock_the_vote_scores")

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
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    print("Loading persons + history...")
    p = load_persons()
    print(f"  {len(p):,} voters")

    print("Scoring with CatBoost (turnout + party)...")
    num_t, cat_t = manifest_features("turnout", set(p.columns))
    tm = load_model(C.ARTIFACTS / "baseline_turnout.cbm")
    cb_turnout = tm.predict_proba(prepare(p, num_t, cat_t))[:, 1]

    num_p, cat_p = manifest_features("party", set(p.columns))
    pm = load_model(C.ARTIFACTS / "baseline_party.cbm")
    cb_party = pm.predict_proba(prepare(p, num_p, cat_p))

    out = pd.DataFrame(index=p.index)
    for c in DETAIL + HISTORY:
        if c in p.columns:
            out[c] = p[c].to_numpy()
        else:
            print(f"  note: {c} not present, skipped")
    out["y_turnout_actual_2024"] = p["y_turnout"].to_numpy()

    out["cb_turnout_prob"] = cb_turnout.astype(np.float32)
    out["cb_dem_lean_prob"] = cb_party[:, 0].astype(np.float32)
    out["cb_rep_lean_prob"] = cb_party[:, 1].astype(np.float32)
    out["cb_other_prob"] = cb_party[:, 2].astype(np.float32)

    # GTN scores, keyed on the sequential person_id evaluate.py wrote.
    gtn = pd.read_parquet(C.SCORES_PARQUET)
    gtn = gtn.set_index("person_id").reindex(p["person_id"].to_numpy())
    out["gtn_turnout_prob"] = gtn["turnout_propensity"].to_numpy(np.float32)
    out["gtn_dem_lean_prob"] = gtn["p_dem_lean"].to_numpy(np.float32)
    out["gtn_rep_lean_prob"] = gtn["p_rep_lean"].to_numpy(np.float32)
    out["gtn_other_prob"] = gtn["p_other"].to_numpy(np.float32)
    out["split"] = gtn["split"].to_numpy()

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
        rng = np.random.default_rng(args.seed)
        parts = []
        for _, g in out.groupby("party", observed=True):
            parts.append(g.sample(max(1, int(round(n * len(g) / len(out)))),
                                  random_state=args.seed))
        idx = pd.concat(parts).index
        samp = out.loc[idx].sample(frac=1.0, random_state=args.seed)
        csv = args.out_dir / f"voter_scores_sample_{len(samp)}.csv"
        samp.to_csv(csv, index=False)
        print(f"Wrote {csv}  ({len(samp):,} rows, "
              f"{csv.stat().st_size / 1e6:.1f} MB, stratified by party)")

    print("\n-- score distributions " + "-" * 30)
    for col in ("cb_turnout_prob", "gtn_turnout_prob",
                "cb_dem_lean_prob", "gtn_dem_lean_prob"):
        s = out[col]
        print(f"  {col:20s} mean={s.mean():.3f}  p10={s.quantile(.1):.3f}  "
              f"p50={s.quantile(.5):.3f}  p90={s.quantile(.9):.3f}  "
              f">0.90={100 * (s > 0.9).mean():.1f}%")

    print("\n-- turnout by cohort (CatBoost) " + "-" * 22)
    nv = out["held_out_of_turnout_fit"] == 1
    for label, m in (("held out of fit (no pre-2024 ballots)", nv),
                     ("everyone else", ~nv)):
        print(f"  {label:38s} n={m.sum():>9,}  mean={out.loc[m, 'cb_turnout_prob'].mean():.3f}"
              f"  actual={out.loc[m & (out['y_turnout_actual_2024'] >= 0), 'y_turnout_actual_2024'].mean():.3f}")


if __name__ == "__main__":
    main()
