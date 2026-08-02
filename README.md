# Nassau/Suffolk Voter Canvass Platform

A full-stack web app for voter canvassing and targeting in Nassau and Suffolk Counties (NY). Built for AD-12 Suffolk. Combines a live Supabase voter database with an interactive map, donor research, election results, and AI-powered targeting.

## Stack

| Layer | Tech |
|---|---|
| Frontend | Next.js 15 (App Router), React, Leaflet |
| Backend | Supabase (Postgres + Auth + RLS) |
| Scoring model | CatBoost (Python) |
| AI targeting | Claude (`@anthropic-ai/sdk`) |
| Deploy | Vercel |
| Automation | GitHub Actions |

## Pages

| Route | Description |
|---|---|
| `/search` | Name/address voter lookup |
| `/map` | Interactive canvass heatmap with household detail panel, AD/city filter, and "show all addresses" toggle |
| `/donations` | Donor search and giving stats |
| `/election-map` | Election results map (2024 general) |
| `/green-map` | Environmental issues overlay map |
| `/target` | AI-powered voter targeting via natural language prompt (**admin only**) |
| `/admin` | Admin panel |

## Running locally

```bash
npm install
npm run dev
```

Requires a `.env.local` with Supabase credentials and an Anthropic API key:

```
NEXT_PUBLIC_SUPABASE_URL=...
NEXT_PUBLIC_SUPABASE_ANON_KEY=...
SUPABASE_SERVICE_ROLE_KEY=...
POSTGRES_URL=...
ANTHROPIC_API_KEY=...
```

## Scoring model

`model/score_voters.py` writes model scores back to the `people` table. It no
longer trains anything: it reads the artifacts the `model/` pipeline produces, so
there is exactly one definition of every feature. See `model/README.md`.

- **`turnout_prob`** — probability this voter turns out in the **November 2026**
  general
- **`dem_lean_prob`** — probability this voter leans Democratic
- **`rep_lean_prob`** — probability this voter leans Republican

`turnout_prob` is scored from as-of-2026 history and levelled to a midterm, so
it is not comparable to a presidential-year score — the same voter sits ~21
points lower than they would for 2024. Summing it over a list estimates a ballot
count. See `model/README.md` for why the level is a separate correction from the
ranking.

```bash
pip install -r model/requirements.txt
python model/score_voters.py            # dry run: report only, writes nothing
python model/score_voters.py --write    # actually UPDATE the database
python model/score_voters.py --model gtn --write   # serve GTN instead of CatBoost
```

Write-back is opt-in: without `--write` this reports the score distribution and
exits. It is keyed on `people.id` (carried through the ETL as `person_uuid`), an
exact primary-key match rather than a name join.

### ACS demographics — two live paths

Both are current, at different granularities and for different consumers:

- **DB side, census tract** — `build/fetch_acs.py` loads into Postgres via
  `supabase/migrations/010_acs_tract_features.sql`. This is what the app reads.
- **Model side, census block group** — `model/features_acs.py` joins into
  `acs_features.parquet` alongside the pipeline's other artifacts.

The tract-level DB path was the newer of the two, but pulling those features back
out of Supabase timed out, so the model pipeline kept its own block-group join
rather than depending on the DB. That is the same constraint that
`model/refresh_cache.py` exists to work around — neither is legacy, and the model
side is the finer granularity.

The `model/` directory also contains a more advanced GTN (Graph Transformer Network) pipeline — see [model/README.md](model/README.md) for details.

## Database

Supabase Postgres. Migrations live in `supabase/migrations/` and run in numbered order:

| Migration | What it does |
|---|---|
| `001` | Initial schema (people, households, elections) |
| `002` | Row-level security policies |
| `003` | Unique constraints |
| `004` | Election results table |
| `005` | People unique index |
| `006–008` | Search and trgm indexes, profiles |
| `009` | Targeting indexes |
| `010` | ACS tract features table |

## Automation

A GitHub Actions workflow (`weekly-donation-refresh`) runs every Sunday at midnight Eastern and refreshes donation data in the DB.

## Build pipeline (legacy static tool)

The original static-HTML canvass tool (no server, embedded dataset) still exists in `build/` and `dist/`. It predates the current web app and targets AD-13/AD-15 Nassau. To rebuild it from a fresh voter file export:

```bash
cd build
pip install -r requirements.txt
python build.py
```

Output: `dist/voter_lookup.html` — a self-contained ~3 MB file with the full dataset gzip+base64-embedded.

## Data handling

The voter file contains real personal information for ~67K registered voters (names, ages, addresses, party, vote history). Keep this repo **private**. Anyone with access to `dist/voter_lookup.html` or a DB dump can extract the full dataset.
