# GTN voter model

Implements `graph_transformer_research.md`: a GraphGPS (GPSConv) multi-task
graph transformer predicting **turnout propensity** and **party support** for
every voter in the Nassau/Suffolk file, benchmarked against a CatBoost
baseline on identical features and splits.

## Pipeline (run in order)

```
pip install -r model/requirements.txt

python model/etl.py               # households -> persons.parquet (~2M rows)
python model/splits.py            # whole-ED 80/10/10 spatial holdout
python model/features_acs.py      # Census block-group demographics join
python model/baseline_catboost.py # the bar to beat -> baseline_metrics.json
python model/graph_build.py       # 5-edge-type graph -> graph.pt
python model/pe_rwse.py           # random-walk PE -> graph_rwse.pt
python model/train.py             # GPSConv training -> gtn_best.pt
python model/evaluate.py          # calibration, head-to-head, scores.parquet
```

Every artifact lands in `model/artifacts/` (gitignored). Smoke-test any stage
on a subset with `python model/etl.py --county NASSAU --city "GLEN COVE"` and
pass the `*_smoke` artifacts through the later stages with `--persons/--graph`.

## Labels (and the leakage rule)

- **Turnout propensity** = `tier_count >= 3`. The `tier` field *is* vote
  history, so tier-derived features are banned from everything the turnout
  task sees. This is enforced by `manifest.yaml`, the single source of truth
  both models read; nothing hardcodes feature lists.
- **Party support** = 3 classes folding NY fusion parties (DEM+WOR / REP+CON /
  other minor). Registration is the training label, so it is *excluded* from
  the party task's features; unaffiliated (BLK) voters are masked in training
  and scored at inference — they are the product output.
- Household/ED party-share features are computed excluding self.
- If the full NY voter file (per-election history) is ever obtained, swap
  `y_turnout` in `etl.py` for "voted in election E" with features computed
  through E-1; the rest of the pipeline is unchanged.

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
