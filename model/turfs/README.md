# Objective #2 — canvass turfs (Phase 1 of 2)

Extends the GTN/CatBoost voter model (`model/README.md`) with a targeting
layer: chunk Democratic-leaning, low-turnout supporters into walkable
canvass turfs, ranked by expected additional Dem ballots. Built entirely
from served scores — **no retrain, no graph embeddings**. See
`model/doc/target_strategies.md` §"OBJECTIVE 2" for the original proposal
this splits in two.

## What's here vs. what isn't

| | status |
|---|---|
| **Phase 1 — turfs** (`turfs.py`, this file) | implemented, tested against synthetic fixtures |
| **Phase 2 — DGI embeddings + HDBSCAN clustering** | not built; gated behind the Stage A screen below |

Phase 2 answers a different question — "is there latent social structure the
model doesn't already know about?" — and needs a Deep Graph Infomax auxiliary
head + `train.py` retrain to answer honestly (clustering on *supervised*
embeddings is circular: see `target_strategies.md`'s objective-2 write-up).
Phase 1 never makes that claim; it chunks by geography and household
structure, which is why it doesn't need the retrain and can ship first.

**Before building Phase 2**, run the residual-ICC screen: cluster the
turnout-model residual (actual − predicted) with (a) whole-ED, (b) Hilbert
turfs from `turfs.py`, and (c) the *existing* GTN's supervised node
embeddings (no new training required for this step). If the supervised
embeddings — the best case, since they carry both structure and label
information — do not clearly beat geographic-only turfs on residual ICC
(ANOVA estimator, not `between/total` — the naive ratio ranks clusterings by
cluster size, not structure), the DGI retrain buys nothing. This is a
necessary-not-sufficient check: it can only tell you to stop, not that Phase
2 will work.

## What `turfs.py` does

1. `load_targets()` scores persons the same way `score_voters.py` /
   `export_scores.py` do (`catboost_util.score_persons`, base-rate shifted
   via `calibration.to_base_rate`), then filters to voters with
   `MOVABLE_LO <= turnout_prob <= MOVABLE_HI` and `support_prob >= SUPPORT_MIN`
   (config constants, `model/config.py`). A guard
   (`assert_serve_base_rate`) refuses scores that aren't on the post-shift,
   served-vintage scale — using the wrong scale doesn't error visibly, it
   just silently deletes a slice of the most valuable voters from the pool
   (a pre-shift 0.88 reads as "already banked"; post-shift it's ~0.70,
   squarely in the movable band).
2. `build_households()` groups targets into households using
   `persons.parquet`'s own `household_row` — **not** something reconstructed
   from graph edges. `household_row` is the real per-record household from
   the ETL; it does not need `scipy.sparse.csgraph.connected_components`,
   because there's no separate relation to union it with. "Address" (a
   building) is a distinct, coarser grouping — the same
   `county|city|street_name|address_number` string
   `graph_build.py`'s `same_address_edges` groups on — and is used **only**
   for the facility flag (`is_facility`, matching `graph_build.py`'s
   `HOUSEHOLD_CLIQUE_CAP`), never to touch household spillover. Conflating
   the two turns every multi-unit building into one giant "household," trips
   the facility cap, and silently zeroes spillover for ordinary households
   inside it — `test_apartment_building_is_not_one_household` pins this.
3. `build_turfs()` Hilbert-sorts geocoded households and greedily chunks them
   into turfs of `TURF_TARGET_DOORS` (overshoot to `TURF_MAX_DOORS` when the
   next household is within `TURF_MERGE_METRES`; force-split when a gap
   exceeds `TURF_BREAK_METRES`), then sweeps every undersized fragment (not
   just a trailing one) into its nearest-centroid neighbour with spare
   capacity. Ungeocoded households get `turf_id == -1` — present in the
   output, excluded from turfs, never silently dropped.
4. `value_households()` / `value_turfs()` implement the valuation formula:
   for each household, the member with the largest movability-weighted mass
   `m_i` is who gets asked (`A(h)`); everyone else contributes the Nickerson
   spillover discount `SPILLOVER_BETA`. Turf value is the household sum,
   reported alongside `canvasser_hours` and `hours_per_ballot` so the ranking
   is legible in real units, not an unnormalized product of ratios.
5. `assign_arms()` does geographically blocked randomization: turfs are
   grouped into small contiguous super-turfs (by Hilbert order, so spatially
   adjacent) before the control/treatment draw, plus an optional buffer ring
   of unanalysed turfs next to every control super-turf — because turfs tile
   contiguous space, individual-turf randomization would let household
   spillover cross the arm boundary at every edge (Eckles et al. 2017).

## Known gaps against the full plan

These need real data (`persons.parquet` — PII, lives outside the repo under
`config.ARTIFACTS`) to resolve and were **not** measured here:

- The actual size of the movable/Dem pool at the current served vintage.
- The true conditional mean of `dem_lean_prob` given `dem_lean_prob >= 0.55`
  (scales every value estimate linearly — don't assume 0.80).
- `SPILLOVER_BETA = 0.60` is Nickerson's 2002 Denver/Minneapolis estimate,
  not a local one. Re-estimate from `elections.parquet`'s within-household
  turnout concordance before trusting the ranking (this is an upper bound on
  spillover, not a causal estimate — homophily inflates it too).
- `assign_arms`'s super-turf adjacency is derived from Hilbert-sort turf-id
  order, not true recomputed centroids, so it's an approximation of
  contiguity rather than exact — fine at the block sizes here
  (`superturf_size=4`), worth revisiting before a real experiment ships.

## Running it

```
python model/turfs/turfs.py                    # --model catboost (default)
python model/turfs/turfs.py --model gtn
python model/turfs/turfs.py --persons PATH --serve-history PATH   # smoke subset
python model/turfs/test_turfs.py                # self-checks, no DB/pipeline needed
```

Writes `turfs.parquet` (one row per turf, non-PII, shareable) and
`turf_assignment.parquet` (one row per target voter, joins back to
`persons.parquet` — not shareable) under `config.ARTIFACTS`, both
fingerprint-stamped the same way every other derived artifact in this
pipeline is (`persons_io.write_stamped`).

`turf_id`, `m_i`, and turf value must never be fed back into the turnout or
party model as a feature — they're derived from the model's own scores, so
that would be circular and would look like a large accuracy gain.
`config.validate_spec()` rejects any manifest feature named `turf_*`,
`value_dem_*`, or `m_i` for exactly this reason.
