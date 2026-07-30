# GTN voter model

Implements `graph_transformer_research.md`: a GraphGPS (GPSConv) multi-task
graph transformer predicting **turnout propensity** and **party support** for
every voter in the Nassau/Suffolk file, benchmarked against a CatBoost
baseline on identical features and splits.

## Model performance history

| Date | Script | Iterations | Features | Turnout AUC | Turnout PR-AUC | Party Acc | Notes |
|---|---|---|---|---|---|---|---|
| 2026-07-22 | score_voters.py | 150 (quick) | voting history, donations, HH/ED party mix, geo | 0.9167 | 0.9712 | 80.3% | Smoke test / dry run — no DB write |
| 2026-07-22 | score_voters.py | 800 | voting history, donations, HH/ED party mix, geo | 0.9231 | 0.9739 | 80.3% | First full run — wrote turnout_prob + dem_lean_prob to DB |
| 2026-07-23 | score_voters.py | 800 | same | 0.9232 | 0.9740 | 80.2% | Re-run to add rep_lean_prob write-back; saved .cbm artifacts |

Split: 80/10/10 spatial holdout on election district. No ACS demographic features yet (not loaded into DB).

## Pipeline (run in order)

```
pip install -r model/requirements.txt

python model/refresh_cache.py     # Supabase -> local Parquet cache (the ONLY
                                  #   thing that reads the DB; slow, resumable)
python model/etl.py               # households -> persons.parquet (~1.9M rows)
                                  #            + elections.parquet (~20M ballots)
python model/splits.py            # whole-ED 80/10/10 spatial holdout
python model/features_acs.py      # Census block-group demographics join
python model/features_history.py  # as-of-cutoff vote-history features
python model/baseline_catboost.py # the bar to beat -> baseline_metrics.json
python model/graph_build.py       # 5-edge-type graph -> graph.pt
python model/pe_rwse.py           # random-walk PE -> graph_rwse.pt
python model/train.py             # GPSConv training -> gtn_best.pt
python model/evaluate.py          # calibration, head-to-head, scores.parquet
```

Or `bash model/run_pipeline.sh` for the whole thing with per-stage logs
(`--from`/`--to` to resume, `--quick` for a smoke run, `--list` for stage names).
`refresh_cache.py` is deliberately NOT a stage: it is slow and timeout-prone, so
it is run on purpose rather than on every pipeline run. `etl.py --source`
defaults to `cache`; pass `--source csv` to build from `data/*_Unrolled.csv`
instead.

Three scripts sit outside the ordered pipeline:

```
python model/backtest_temporal.py # train on 2020, predict 2024 (transfer gap)
python model/export_scores.py     # scored voter file for eyeballing (PII; writes
                                  #   to config.SCORES_DIR, outside the repo)
python model/score_voters.py      # dry run; --write to update Supabase
```

Self-checks, none of which need the database or a built pipeline:

```
python model/test_features_history.py   # as-of feature semantics
python model/test_splits.py             # split-label coverage and validation
python model/test_catboost_util.py      # categorical rendering, leakage guards
python model/test_sources.py            # donation date parsing
python model/test_config.py             # PII destination rules
python model/test_refresh_cache.py      # atomic cache dump
```

Every artifact lands in `config.ARTIFACTS`, which is **outside this repo** —
`C:\data\rock_the_vote_artifacts` by default, beside the Supabase cache. It is
not in the repo because `persons.parquet` carries names, addresses, ages and
party registration for ~1.85M people, every other artifact joins back to it on
`person_row`/`person_id`, and this tree is OneDrive-synced. Set `RTV_PII_ROOT`
to relocate both; `config.pii_dest()` refuses any path inside the repo and fails
at import, so a misconfigured root stops the pipeline rather than leaking.

Smoke-test any stage on a subset with
`python model/etl.py --county NASSAU --city "GLEN COVE"` and pass the `*_smoke`
artifacts through the later stages with `--persons/--graph`.

`features_acs.py` and `features_history.py` derive side files rather than
mutating `persons.parquet`, and each is stamped with a fingerprint of the
persons table it came from. `persons_io.load_persons()` verifies that stamp and
refuses a mismatch, naming the stage to rerun. This matters because `person_id`
and `person_row` are row ordinals: two different populations of the same size
share every id, so a length check cannot tell a current side file from a stale
one — it would join a voter's row to a different voter's features in silence.

## Labels (and the leakage rule)

- **Turnout propensity** = actually voted in the target general
  (`config.TARGET_GENERAL_YEAR`, currently 2024). Voters not yet 18 by that
  election carry `y_turnout = -1` and are masked from training and metrics
  (they still get scored). Voters with no ballots before the target general are
  scored but held out of the turnout *fit*: the export contains only voters with
  ≥1 lifetime ballot, so "no prior history" mechanically implies "voted E"
  (P = 0.959) — a property of who is in the file, not of anyone's behaviour.
  The leakage rule is temporal: everything the turnout task sees must
  be as-of the target general (`as_of` in `manifest.yaml`); export-computed
  summaries that span the cutoff (`tier_*`, vote-count aggregates, and the
  household canvass `score_*` columns derived from them) are marked
  `spans_cutoff` and asserted out of the turnout task. Donation features and
  co-donor edges are date-filtered to before election day in `sources.py`.
  `manifest.yaml` is the single source of truth both models read; nothing
  hardcodes feature lists. `python model/test_features_history.py` pins the
  as-of semantics with synthetic voters.
- **Party support** = 3 classes folding NY fusion parties (DEM+WOR / REP+CON /
  other minor). Registration is the training label, so it is *excluded* from
  the party task's features; unaffiliated (BLK) voters are masked in training
  and scored at inference — they are the product output.
- Household/ED party-share features are computed excluding self.

## Vote history (the `*_Unrolled` files)

`etl.py --source csv` reads `data/*_Unrolled.csv`, whose `household_detail` carries every
voter's full per-election history (~1999-present, GENERAL + PRIMARY, with
vote method). It lands in `elections.parquet` (person_row, year, etype,
method), and `features_history.py` turns it into `hist_*` features computed
**as of the target general E** (`config.TARGET_GENERAL_YEAR`): only ballots
from years < E plus the year-E primary. It also writes `y_voted_general_E` —
the real turnout outcome.

`hist_*` features feed the shared encoder (y_turnout is the real year-E
outcome, so they are legitimate for both tasks). Exceptions: pure
primary-participation features are `turnout_head`-only — NY primaries are
closed, so BLK voters (the party task's scoring population) structurally
cannot have them — and the leave-self-out household/ED rate aggregates stay
heads-only like the registration shares. `backtest_temporal.py` measures the
question that matters for 2026: train on one general, predict the next
(donation features excluded there — they are as-of the config target only).
To score the 2026 general, set `TARGET_GENERAL_YEAR = 2026` and rerun the
pipeline: every feature then uses history through the 2026 primary, and the
label column is vacuously zero (the election hasn't happened).

History caveats: coverage starts ~1999 and only includes ballots cast while
registered in-county (movers' rates are understated); ages are as of the
export date and get de-aged per election year; `tier_count` is a lossy
summary of this history (corr ~0.7 with true lifetime ballots).

## Graph

Person nodes (parquet row order). Edge types, coalesced by priority:
household clique (capped at 10 — larger records are facilities and get
sampled peers), same-address, donation co-occurrence (conduits like ActBlue
excluded; committees capped at 5k donors), spatial kNN over household points,
sampled ED peers. Training clusters are whole EDs snake-ordered by lat/lon
(~8k persons each) — ClusterGCN-style, chosen because this machine
(Windows-on-ARM, CPU-only torch) can't run pyg-lib/torch-sparse samplers.
RWSE is exact within each cluster (sparse-CSR power iteration).

## Model

`gtn.py`: encoder = numeric features + categorical embeddings + RWSE
projection -> 3x GPSConv(GINEConv, performer attention, edge-type embedding).
Two heads: turnout (+ own-party embedding), party (+ tier features). BCE +
masked CE, equal weights. `train.py` early-stops on val loss;
`evaluate.py` applies per-head temperature scaling fitted on val, reports
test metrics, ED-aggregate MAE (predicted vs actual rates on held-out EDs),
reliability diagrams, and writes `scores.parquet` with calibrated
probabilities for all ~1.88M voters.

## Data notes

- Donation features/edges come from the committed `dist/nyboe-data.b64` and
  the `fec_donations` block inside `dist/nassau-data.b64` when the gitignored
  raw caches are absent.
- ACS comes from the keyless Census table-based summary files (the Data API
  now requires an API key).
- Geocoding reuses `build/build.py`'s TIGER interpolator; results are cached
  in `geocode_cache_*.parquet` under `config.ARTIFACTS`.
