# CLAUDE.md

## Project

**Bellwether (rock_the_vote)** is a voter analytics platform for Nassau and Suffolk counties, NY.

- **Next.js web app** (repo root): the Bellwether dashboard for field directors and campaign managers.
- **`model/`** (Python): the turnout and party-lean pipeline. CatBoost is the serving model, with no retraining this cycle.
- **Canvasser iOS app** (Expo), new: see the design spec at `docs/canvassing_mapping_target.md`. That spec is the source of truth for canvassing features and decisions #1–#14. The app itself lives at `apps/canvass-ios/`, an npm project isolated from the repo root (its own `package.json`, lockfile, eslint, tsconfig and Jest config; the root configs ignore `apps/`).

---

## Hard rules: voter data and secrets

1. **Before every commit,** check `git status` and the diff for data files (`*.csv`, `*.parquet`, `*.xlsx`, `*.db`), for secrets, or for anything that looks like names with addresses. If in doubt, don't commit. Ask.

---

## Local dev: environment files

| File | Contains | Who uses it |
|---|---|---|
| `.env.local` (repo root, gitignored) | **Real** credentials | User only. Next.js loads it automatically. |
| `C:\data\rock_the_vote_secrets\.env.local` (or `$RTV_PII_ROOT\rock_the_vote_secrets\.env.local`, see `model/config.py`) | Durable master copy of the real credentials, kept outside the repo | User only. `model/db.py` reads it via `config.SECRETS_ENV`. |

**Why the master copy exists:** `.env.local` is gitignored and can't be recovered from git. A lost working tree lost it, and `DATABASE_URL`, on 2026-08-01. The user keeps the master copy outside the repo, restores `.env.local` from it when needed, and keeps the two in sync when credentials change.

---

## Canvasser iOS app

- **Stack (spec §14a):** Expo managed workflow, `expo-dev-client` (Expo Go won't work because of the native modules), EAS Build and EAS Submit, encrypted SQLite, `expo-location` (foreground only, no background tracking), MapLibre, `expo-secure-store` for tokens.
- **iOS only** (spec decision #13, updated 2026-09-21). Android is dropped from this cycle's scope, not merely deferred: one platform to build, test, review and support. Don't add Android-specific code or configuration. Expo keeps Android reachable later without a rewrite.
- **Publisher account (spec §14a, decided 2026-09-21):** Apple Developer Program **individual** account, sole developer. No D-U-N-S number, no organization, no legal entity. Don't reintroduce organization-account language into the docs or code comments.
- **Variants:** `dev`, `preview` and `production`, set via `APP_VARIANT` in `app.config.ts` and matching `eas.json` profiles. Each variant has its own bundle ID and backend.
- **On-device rules:**
  - never store or render turnout or lean scores;
  - render only the fields the API returns (field visibility is enforced server-side);
  - the offline cache is encrypted and wiped at End Shift, after 12 hours idle, at logout, or on a remote-wipe flag.
- **Commands:** `npm test` (Jest). `npx expo start --dev-client` (the user runs it with the phone).
- **Never run `eas build`, `eas submit`, or `eas update` unless the user asks.** They use build credits or ship to real devices.

## Backend, routing, and copilot

- **Routing is deterministic.** No LLM calls anywhere in scoring, targeting, or routing code.
- **Planned structure:** routing service in Python with OR-Tools at `services/routing/`, kept separate from `model/` (the scoring pipeline). SQL migrations for `outcome_events`, `door_leases`, `route_plans`, `access_grants` and `experiment_assignments` go in `supabase/migrations/`, as `NNN_name.sql` continuing the existing sequence.
- **AI ops copilot MCP server:** aggregate `ops.*` views only. **Never add a tool that takes or returns a voter ID, household ID, name, or score.** Keep small-cell suppression (fewer than 10 households) in the views.

---

## Working style

- **Follow the spec.** If the code has to deviate, say so and propose the spec edit. Don't silently diverge.
- **Keep tasks small:** one feature per branch.
- **Write tests first** for the risky logic:
  - lease claim and expiry,
  - outbox idempotency (`client_event_id`),
  - cache wipe,
  - field visibility,
  - retry and outcome rules,
  - the "no scores in API responses" check.
- **Before saying a task is done,** run the tests and lint, and report exactly what ran and the result.
- **Flag these for the user's line-by-line review:** authentication and keys, row-level security policies, lease SQL, device encryption and wipe, and any endpoint that returns voter fields.
