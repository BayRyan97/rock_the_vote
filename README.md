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

`model/score_voters.py` trains two CatBoost classifiers directly against the Supabase DB and writes scores back to the `people` table:

- **`turnout_prob`** — probability this voter turns out in the next general
- **`dem_lean_prob`** — probability this voter leans Democratic

```bash
pip install -r build/requirements.txt
python3 model/score_voters.py           # full run (~800 iterations)
python3 model/score_voters.py --quick   # smoke test (150 iterations)
python3 model/score_voters.py --dry-run # score without writing to DB
```

ACS tract-level demographic features are fetched via `build/fetch_acs.py` and joined at the census tract level. The DB migration for the ACS feature table is in `supabase/migrations/010_acs_tract_features.sql`.

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
