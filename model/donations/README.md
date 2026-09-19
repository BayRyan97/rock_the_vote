# Objectives 3 & 4 — donation targeting

All donation **modeling** lives here. Sibling of `model/turfs/` (objective 2)
and built the same way: a targeting layer on top of the served scores, no
retrain, no graph embeddings. See `model/doc/target_strategies.md`
§"OBJECTIVE 3" and §"OBJECTIVE 4" for the research this implements.

Data **acquisition** stays in `build/` with the other fetchers
(`fetch_fec.py`, `fetch_fec_bulk.py`, `fetch_nyboe.py`) — that is where
`.github/workflows/refresh_donations.yml` calls from, and splitting the weekly
ETL across two packages would mean the workflow reaching into `model/`.

| | where | status |
|---|---|---|
| DB hygiene + aggregate refresh | `build/refresh_donation_aggregates.py` | **done** |
| Schema for the above | `supabase/migrations/022_donations.sql` | **written, not yet applied** |
| FEC committee/candidate master fetch | `build/fetch_fec_committees.py` | to build |
| Committee party + labor dimension | `committees.py` | to build |
| Revealed lean + donor propensity | `donor_value.py` | to build |
| Push to Supabase | `write_supabase.py` | to build |
| Self-checks (no DB, no CatBoost) | `test_donations.py` | to build |

## What this can and cannot answer

**Objective 4 asks for donor ROI — incremental uplift, τ_i = E[amount |
solicited] − E[amount | not solicited]. That is not estimable here.** There is
no record anywhere in this repo or database that any person was ever *asked*
for money: no solicitation log, no send log, no ask amount. `door_knocks`
exists but is canvassing, has no money dimension, and no application code reads
or writes it.

So this package ships **propensity**, named as propensity, exactly as
`target_strategies.md` Recommendation 4 requires — "do not deploy a propensity
ranking as 'ROI' … until then, label propensity outputs honestly as
propensity." `write_supabase.py` enforces that in code: it refuses to write any
outbound column whose name contains `roi`, `uplift`, or `lift`. Migration 023
adds the randomized ask-holdout that makes real uplift estimable next cycle;
it is impossible to backfill, which is why it goes in before the model that
would use it.

## Why committee partisanship is the first thing built

Measured against production, 2026-08-07:

| BLK (unaffiliated) donors | n | avg model `dem_lean_prob` |
|---|---|---|
| gave to DEM committees only | 7,918 | **0.480** |
| gave to REP committees only | 3,141 | 0.277 |

The party model separates them directionally but is badly under-confident:
7,918 people who have **only ever** given money to Democrats are scored as
coin-flips. Cross-party giving runs both ways — 2,370 registered Democrats gave
$2.6M to Republican committees.

Today none of this is usable, because `donations.committee` is free text and
the only partisan tagging in the codebase is a two-element hardcoded set
(`DEM_CONDUITS={"ACTBLUE"}`, `REP_CONDUITS={"WINRED"}` in `model/config.py`).

### The union confound, which inverts the signal if ignored

16,029 registered Republicans gave $6.3M to nominally Democratic-side
committees — and the single largest committee in the entire file is a
teachers-union COPE PAC (105,291 rows, 13,327 donors). Union payroll deduction
signals **membership, not partisanship**. A Republican's automatic $8/paycheck
NYSUT deduction must never read as Democratic support.

`revealed_lean` is therefore computed over side-tagged **non-labor** dollars
only. Labor dollars do not vanish — they become their own features. Union
membership is a genuinely useful targeting signal; it is just a *different*
signal, and the point is to stop the two being summed.

### Why the tagging is nearly free

`build/fetch_fec_bulk.py:load_committee_names()` already downloads the FEC
committee master (`cm{yy}.zip`) on every run and keeps only fields 0 and 1
(`CMTE_ID`, `CMTE_NM`). It discards `CMTE_PTY_AFFILIATION` (10) and `ORG_TP`
(12, `'L'` = labor) — which is precisely the party-and-union answer. And
`donations.committee` stores `CMTE_NM` verbatim, so an exact normalized-name
join covers the FEC side. No Splink, no fuzzy matching.

A name regex is not an acceptable substitute: it leaves the **largest** bucket
unclassified — 393,718 gifts, 80,986 donors, $128M.

NY BOE genuinely has no party field (`build/fetch_nyboe.py` selects only
`cand_comm_name`), so those committees stay `side = NULL` except for a curated
labor list. Phase 2 excludes NULLs from the denominator, which is the honest
treatment — absence of a tag is not evidence of neutrality.

## Known gaps

- `donor_key` is `UPPER(name)|UPPER(city)|zip5`, a deterministic generated
  column — not the probabilistic linkage objective 3 calls for. It over-merges
  two same-name people in the same city into one donor. Measure the rate before
  deciding whether Splink is worth its blast radius.
- Donation features are cut at the 2024 donation cutoff in **both** vintages
  (`model/README.md`), so a 2025 first-time donor looks like a non-donor at
  serving time. `revealed_lean` inherits this until the serve vintage gets its
  own cutoff.
- Committee identity is a **name**, not `cmte_id` — the id is resolved and then
  dropped before the DB write. A committee that renames between cycles becomes
  two committees. `committees.py` reports the count of names carrying
  conflicting party across ids rather than silently picking one; a non-trivial
  count is the signal to start persisting `cmte_id`.
- Employer/occupation edges (objective 3) have no data — but they are
  **recoverable, not absent**: FEC `indiv` carries them at indices 11 and 12 and
  `fetch_fec_bulk.py` simply keeps 6 of 15 fields.

## Running it

```
python build/refresh_donation_aggregates.py            # dry run, reports drift
python build/refresh_donation_aggregates.py --write    # apply
python model/donations/test_donations.py               # self-checks, no DB needed
```

Migration `022_donations.sql` must be applied first; the refresh script checks
and names it if missing.
