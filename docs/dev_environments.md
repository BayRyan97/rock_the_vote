# Dev and preview environments (task S-04)

**What this is:** three Supabase projects behind one codebase — `prod`, `dev`
and `preview` — plus a loader that seeds the non-production ones with a small
slice of the real voter data.

**Why it exists.** Until now there has been exactly one database, and it is the
live one: ~1.85M people, the scores the field is working from. Every migration
test, every `TRUNCATE`, every score write-back has been aimed at it. That is
the thing this removes. It also unblocks the canvasser app, which needs a
backend it can point at without a bug in a draft endpoint touching live data.

**Why real data rather than fixtures.** The spec's decisions are quantitative —
the lean floor at 0.30, the unit cap of 10, turf sizing at 150 doors. Synthetic
rows cannot tell you that lowering a floor adds 12% more doors, or that a turf
has three apartment buildings in it. Dev is seeded from the real cache so those
numbers mean something. It is seeded with **8 turfs**, not all 1,652, so the
second cloud project holds roughly 1,100 households instead of 758,000.

---

## One-time setup

### 1. Create the two Supabase projects — 👤 Tim

Claude cannot do this: it needs the Supabase account, and each project is a
billable resource on the plan.

At [supabase.com/dashboard](https://supabase.com/dashboard), create two
projects in the same organization as production:

| Project | Suggested name | Used by |
|---|---|---|
| dev | `rock-the-vote-dev` | `npm run dev`, Claude Code sessions, the `dev` app variant |
| preview | `rock-the-vote-preview` | EAS preview builds, the `preview` app variant |

The free tier is enough for both at this data volume. Then, for each project,
collect from **Project Settings → Database** and **→ API**:

- the project URL and the `anon` publishable key,
- the `service_role` secret key,
- the connection string (session mode, port 5432).

### 2. Apply the schema — 👤 Tim (or Claude, once the URLs exist)

```bash
supabase link --project-ref <dev-project-ref>
supabase db push
```

`supabase/migrations/` holds all 45 migrations, so both projects end up with
the production schema, including the row-level security policies. Run it again
with the preview ref.

### 3. Write the environment files — 👤 Tim

Both files are gitignored and the CI guard blocks them from being committed.

`.env.development.local` (repo root) — the **dev** project's values, in the
same four variables `.env.local` already uses:

```
NEXT_PUBLIC_SUPABASE_URL=...
NEXT_PUBLIC_SUPABASE_ANON_KEY=...
SUPABASE_SERVICE_ROLE_KEY=...
DATABASE_POOL_URL=...
```

Next.js loads `.env.development.local` ahead of `.env.local` when
`NODE_ENV=development`, so `npm run dev` gets dev and `npm run build && npm
start` still gets production. The switch has to happen at the file level
because `NEXT_PUBLIC_*` values are inlined into the client bundle at build
time — a runtime variable cannot move them.

Then add to **`.env.local`** (and to the durable master copy at
`C:\data\rock_the_vote_secrets\.env.local`, which is the one that survives a
lost working tree):

```
DATABASE_URL_DEV=postgresql://postgres.<dev-ref>:PASSWORD@aws-1-...pooler.supabase.com:5432/postgres
DATABASE_URL_PREVIEW=postgresql://postgres.<preview-ref>:PASSWORD@aws-1-...pooler.supabase.com:5432/postgres
```

`model/` and `scripts/` switch by variable rather than by file, since they have
no build step. `DATABASE_URL` stays production, so every existing pipeline
command behaves exactly as before.

---

## Seeding the data

```bash
python scripts/load_dev_env.py --target dev --dry-run
```

Reports what would be loaded and contacts no database. Then:

```bash
python scripts/load_dev_env.py --target dev --turfs 8 --yes
```

Roughly what lands, for 8 turfs (measured 2026-09-26, ~2s to select):

| Table | Rows |
|---|---|
| turfs | 8 |
| households | 1,145 |
| people | 5,009 |
| turf_assignment | 2,240 |
| donations | 8,317 |
| boe_contacts | 4,642 |
| ev_scores / election_results / donations_meta | loaded whole (small) |

The source is the local Parquet cache at `C:\data\rock_the_vote_cache`, not
production — seconds instead of hours, and no load on the live instance. If the
cache is missing, refresh it first with `python model/refresh_cache.py`, which
is read-only against prod.

### What is deliberately not loaded

- **`profiles`, and anything under `auth`.** Those rows reference `auth.users`
  **in their own project**. Copied across, they point at user ids that do not
  exist. Sign up again in the dev project and set your role there:
  ```sql
  update profiles set role = 'admin' where id = '<your-new-user-id>';
  ```
- **`canvass_notes`.** Field observations are operational. An empty notes table
  is the honest starting state for a dev environment.
- **boe_contacts PII columns.** Email, phone, names and street address are not
  in the cache to begin with (`model/refresh_cache.py:BOE_COLS`), so they
  cannot reach a second cloud project through this path.

---

## Guards

The loader can destroy a database, so two things stand in front of it:

1. **`model/db.py:assert_not_prod()`** refuses any load whose connection string
   resolves to the production project. It compares the Supabase *project
   reference*, not the URL text, so prod's pooler URL, its session-mode URL and
   its direct URL are all recognised as the same database. If it cannot
   identify the project, it refuses — it fails closed.
2. **`--yes`** is required before anything is written. Without it the script
   prints the destination project and the row counts and stops.

`--target` accepts only `dev` and `preview`. There is no spelling of it that
means production.

Tests: `python model/test_db.py` and `python scripts/test_load_dev_env.py`.
Both run in CI.

---

## Known gap: `turf_assignment.hh_id` is not `households.id`

Worth knowing before writing any query that joins them. `turf_assignment.hh_id`
is an integer row index from the model pipeline; `households.id` is a uuid.
Migration 017 declares `hh_id integer NOT NULL` with **no foreign key**, so
nothing in the database catches the mistake, and joining the two matches zero
rows without raising anything. The route from a turf to a household is:

```
turfs.turf_id -> turf_assignment.person_id -> people.household_id -> households.id
```

The loader and its tests both pin this.
