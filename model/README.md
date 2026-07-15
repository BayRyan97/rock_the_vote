# GTN voter model

Implements `graph_transformer_research.md`: a GraphGPS (GPSConv) multi-task
graph transformer predicting **turnout propensity** and **party support** for
every voter in the Nassau/Suffolk file, benchmarked against a CatBoost
baseline on identical features and splits.

## Pipeline (run in order)

```
pip install -r model/requirements.txt

python model/etl.py               # households -> persons.parquet (~1.9M rows)
                                  #            + elections.parquet (~20M ballots)
python model/splits.py            # whole-ED 80/10/10 spatial holdout
python model/features_acs.py      # Census block-group demographics join
python model/features_history.py  # as-of-cutoff vote-history features
python model/baseline_catboost.py # the bar to beat -> baseline_metrics.json
python model/backtest_temporal.py # train on 2020, predict 2024 (transfer gap)
python model/graph_build.py       # 5-edge-type graph -> graph.pt
python model/pe_rwse.py           # random-walk PE -> graph_rwse.pt
python model/train.py             # GPSConv training -> gtn_best.pt
python model/evaluate.py          # calibration, head-to-head, scores.parquet
```

Every artifact lands in `model/artifacts/` (gitignored). Smoke-test any stage
on a subset with `python model/etl.py --county NASSAU --city "GLEN COVE"` and
pass the `*_smoke` artifacts through the later stages with `--persons/--graph`.

## Labels (and the leakage rule)

- **Turnout propensity** = actually voted in the target general
  (`config.TARGET_GENERAL_YEAR`, currently 2024). Voters not yet 18 by that
  election carry `y_turnout = -1` and are masked from training and metrics
  (they still get scored). The old tier proxy survives as the diagnostic
  column `y_turnout_tier` — it agrees with the real outcome for only ~3/4 of
  voters. The leakage rule is temporal: everything the turnout task sees must
  be as-of the target general (`as_of` in `manifest.yaml`); export-computed
  summaries that span the cutoff (`tier_*`, vote-count aggregates) are marked
  `spans_cutoff` and asserted out of the turnout task. Donation features and
  co-donor edges are date-filtered to before election day in `etl.py`.
  `manifest.yaml` is the single source of truth both models read; nothing
  hardcodes feature lists. `python model/test_features_history.py` pins the
  as-of semantics with synthetic voters.
- **Party support** = 3 classes folding NY fusion parties (DEM+WOR / REP+CON /
  other minor). Registration is the training label, so it is *excluded* from
  the party task's features; unaffiliated (BLK) voters are masked in training
  and scored at inference — they are the product output.
- Household/ED party-share features are computed excluding self.

## Vote history (the `*_Unrolled` files)

`etl.py` reads `data/*_Unrolled.csv`, whose `household_detail` carries every
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
  in `model/artifacts/geocode_cache_*.parquet`.
