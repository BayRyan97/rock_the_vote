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
   `graph_build.py`'s `same_address_edges` groups on. Conflating the two
   turns every multi-unit building into one giant "household" and silently
   zeroes spillover for ordinary households inside it —
   `test_apartment_building_is_not_one_household` pins that.

   It also sets `is_facility`, which asks a different question: is this row a
   door, or a building nobody can knock? Two signals, OR'd, because either
   shape can occur depending on the source — `household_size > TURF_FACILITY_MIN_VOTERS`
   (one ETL row **is** the whole building) or `addr_size > TURF_FACILITY_MIN_VOTERS`
   (the source split units into separate households at one address).

   > **This is a bug fix, 2026-08-03.** Only the second test existed before,
   > and against this ETL it can never fire: households are keyed on
   > `household_uuid`, so `10 WELWYN RD GREAT NECK` is **one** row with 30
   > registered voters and `addr_size == 1`. `is_facility` was therefore
   > `False` for every household ever built and `is_facility_share` was
   > exactly `0.0` on all 1,652 turfs in production. `test_one_row_building_is_a_facility`
   > pins the real data shape; the threshold is its own constant rather than
   > `HOUSEHOLD_CLIQUE_CAP`, which governs GNN edge sampling and cannot be
   > retuned without invalidating a trained model.
3. `split_track()` tags each household `walk`, `facility`, or `ungeocoded`,
   and only the walk track is chunked into turfs. Facilities get
   `turf_id == -2` and are ranked separately by `rank_facilities()`; a
   canvasser cannot knock a locked lobby, and counting a 58-voter tower as
   one 3-minute door is what made apartment-dense turfs look like the most
   efficient walk lists in the county (turf 1443, Great Neck Plaza: 39
   buildings holding 896 of its 1,136 targets, best `hours_per_ballot` in
   Nassau). The density is a real opportunity — it just needs lobby access,
   phone, or relational organising rather than door-knocking.
4. `build_turfs()` Hilbert-sorts walk-track households and greedily chunks them
   into turfs of `TURF_TARGET_DOORS` (overshoot to `TURF_MAX_DOORS` when the
   next household is within `TURF_MERGE_METRES`; force-split when a gap
   exceeds `TURF_BREAK_METRES`), then sweeps every undersized fragment (not
   just a trailing one) into its nearest-centroid neighbour with spare
   capacity **within `TURF_BREAK_METRES`** — without that ceiling the merge
   stage silently undid the water/highway split the chunk loop had just
   forced. A fragment with no neighbour in reach stays undersized, by design.
   Ungeocoded households get `turf_id == -1` — present in the output,
   excluded from turfs, never silently dropped.
5. `value_households()` / `value_turfs()` implement the valuation formula:

   > `V(h) = κ · Δ · (m_A + β · min(Σ_{j≠A} m_j, SPILLOVER_MAX_RATIO · m_A))`

   For each household, the member with the largest movability-weighted mass
   is who gets asked (`A(h)`); everyone else contributes the Nickerson
   spillover discount `SPILLOVER_BETA`. The `min()` is new: β is measured on
   **two-registered-voter** households, and applied linearly it credited one
   knock at a 275-target building with 3.22 expected ballots. Because `A(h)`
   is the argmax, every `m_j ≤ m_A`, so the cap is provably inert for 1- and
   2-person households — exactly the sizes Nickerson covers — and no door can
   exceed `(1 + 3β) = 2.8×` its single best voter.

   Two value columns come out. **`value_net_margin` is what the list is
   ranked on**: mobilisation turns out whoever answers, so a 0.55-lean voter
   is worth `+0.10` net rather than `+0.55` gross. Computed as
   `p_support − p_oppose`, *not* `2p−1` — the party head is 3-class
   (`dem_lean`/`rep_lean`/`other`), so `2p−1` would charge the campaign for
   minor-party registrants as though they voted the other way.
   `value_dem_ballots` is kept alongside it for comparison with the prior
   ranking. **They are different units** — `hours_per_ballot` and
   `hours_per_net_margin` are likewise not interchangeable.
6. `assign_arms()` does geographically blocked randomization: turfs are
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
- `SPILLOVER_MAX_RATIO = 3.0` is a judgement call, not a measurement. It is
  inert on every household size Nickerson actually covers, so it can only bind
  where the prior was already being extrapolated — but the right shape for
  large-household spillover (saturating? per-capita decay?) is unmeasured.
- `CANVASS_CONTACT_RATE = 0.25` is ours, not the literature's, and scales
  every value estimate linearly.
- Facility valuation reuses the door formula, including κ. The contact rate
  for a lobby/phone/relational approach is certainly *not* 0.25, so
  `facilities.value_net_margin` ranks buildings correctly against each other
  but is not comparable in level to a turf's.
- `assign_arms` stratifies on `value_net_margin` as of 2026-08-03. That
  changed arm assignment even under the frozen seed, which was only safe
  because nothing had been fielded yet. **Do not change the stratification
  column again once canvassers are out** — it invalidates the experiment as
  surely as changing `ARM_ASSIGNMENT_SEED`.

## Running it

```
python model/turfs/turfs.py                    # --model catboost (default)
python model/turfs/turfs.py --model gtn
python model/turfs/turfs.py --persons PATH --serve-history PATH   # smoke subset
python model/turfs/test_turfs.py                # self-checks, no DB/pipeline needed
```

Writes three artifacts under `config.ARTIFACTS`, all fingerprint-stamped the
same way every other derived artifact in this pipeline is
(`persons_io.write_stamped`):

| file | one row per | shareable |
|---|---|---|
| `turfs.parquet` | walkable turf, ranked by `value_net_margin` | yes, non-PII |
| `facilities.parquet` | apartment building / facility, ranked separately | yes, non-PII |
| `turf_assignment.parquet` | target voter, joins back to `persons.parquet` | **no** |

Then `python model/turfs/write_supabase.py` (dry run by default, `--write` to
apply) pushes all three. It TRUNCATEs and reloads them in a single statement —
`facilities.nearest_turf_id` FKs `turfs`, and Postgres requires exactly that.
Migrations `017`, `019` and `020` must be applied first; the script checks and
tells you which one is missing.

`turf_id`, `m_i`, and turf value must never be fed back into the turnout or
party model as a feature — they're derived from the model's own scores, so
that would be circular and would look like a large accuracy gain.
`config.validate_spec()` rejects any manifest feature named `turf_*`,
`value_dem_*`, or `m_i` for exactly this reason.
