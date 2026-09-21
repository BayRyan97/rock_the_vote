# Bellwether canvasser app (iOS)

Expo app for canvassers. Source of truth for behaviour: [`docs/canvassing_mapping_target.md`](../../docs/canvassing_mapping_target.md);
rules: [`CLAUDE.md`](../../CLAUDE.md); task list: [`docs/canvass_task_plan.md`](../../docs/canvass_task_plan.md).

This is **task P1-01: the scaffold**. The four tabs render placeholders that
name the task which fills them in. No auth, no local store, no map, no routing
yet.

**iOS only.** `platforms: ['ios']` in `app.config.ts` is what enforces it —
without that key `expo prebuild` and `eas build` will generate an Android
project regardless. Android is out of scope (spec decision #13, updated
2026-09-21); Expo keeps it reachable later without a rewrite.

A separate npm project from the repo root on purpose: the root `eslint.config.mjs`,
`tsconfig.json`, `jest.config.js` and `.vercelignore` all exclude `apps/`.
Install and run everything from this directory.

## Commands

```bash
npm install          # from apps/canvass-ios
npm test             # Jest (jest-expo)
npm run typecheck    # tsc --noEmit
npm run lint         # eslint
npm start            # expo start --dev-client  (needs a dev build on the phone)
```

`npm start` will not work with Expo Go — the app depends on native modules
(`expo-dev-client` now; SQLCipher, MapLibre and secure-store later). It needs a
development build installed on the device (task P1-02).

## Variants

Three build variants, selected by `APP_VARIANT`, resolved in
[`src/config/variants.ts`](src/config/variants.ts):

| `APP_VARIANT` | Name | Bundle identifier | Backend |
|---|---|---|---|
| `dev` (default) | Bellwether Dev | `org.dlfi.bellwether.canvass.dev` | `http://localhost:3000` unless overridden |
| `preview` | Bellwether Pre | `org.dlfi.bellwether.canvass.preview` | `EXPO_PUBLIC_API_URL`, required |
| `production` | Bellwether | `org.dlfi.bellwether.canvass` | `EXPO_PUBLIC_API_URL`, required |

Distinct bundle IDs mean a dev build and a TestFlight build can sit on the same
phone without overwriting each other. `resolveVariant` throws on an
unrecognised `APP_VARIANT`, and on a missing API URL for preview or production,
rather than falling back — a preview build silently pointed at `localhost`
fails on the phone hours later and looks like a network bug.

> The `org.dlfi.bellwether.canvass` prefix is a **placeholder**. It has to match
> the identifier registered under the individual Apple Developer account
> (task S-01). Changing it after the first TestFlight build means a new app
> record in App Store Connect, so confirm it before P1-02.

## Environment variables

| Variable | Where it is set |
|---|---|
| `APP_VARIANT` | `eas.json` per build profile; unset locally means `dev` |
| `EXPO_PUBLIC_API_URL` | EAS environment (`eas env:create`) for preview/production; a shell variable or `.env.local` for local dev |

There is deliberately **no committed `.env.example`** here: `scripts/ci_guard.py`
blocks any `.env*` path from entering this repo, because it is public. This
table is the template.

`EXPO_PUBLIC_*` variables are inlined into the JavaScript bundle and are
readable by anyone with the app. Only public values go there. Secrets stay
server-side (spec §11: all voter reads go through the Bellwether API, never
straight to Supabase from the device).

Running against a dev server from a physical phone needs the host machine's LAN
address, because `localhost` is the phone:

```bash
EXPO_PUBLIC_API_URL=http://192.168.1.20:3000 npm start
```

## EAS

`eas.json` defines three build profiles matching the three variants. Nothing in
this repo runs EAS: per `CLAUDE.md`, `eas build`, `eas submit` and `eas update`
are run by the user only — they spend build credits or ship to real devices.

`extra.eas.projectId` is not committed yet. `eas init` prints the value to add
to `app.config.ts`; it cannot write it automatically, because this project uses
a dynamic config rather than `app.json`.

These App Store items are intentionally left undecided rather than guessed.
All belong to task R-02:

- **Export compliance** (`ios.config.usesNonExemptEncryption`) is unset, so
  App Store Connect asks at submission time. The app will use SQLCipher (P1-04),
  so this is a real declaration to make, not a checkbox to default.
- **Privacy labels** — precise location, user IDs.
- **Developer name.** The app ships from an **individual** Apple Developer
  account (spec §14a), so the App Store shows the account holder's legal name
  as the seller. It is set when the first app record is created and can't be
  edited in App Store Connect afterwards.
- **Support and privacy-policy URLs** — both are published on the listing, so
  neither should resolve to a personal address.
- **Territory availability.** Excluding the 27 EU territories keeps the EU
  Digital Services Act trader contact details (address, phone, email) off the
  product page. The seller name still shows in every storefront.

## Layout

```
app.config.ts            variant-aware Expo config (replaces app.json)
eas.json                 dev / preview / production build profiles
src/app/                 routes only — never put components or helpers here
  _layout.tsx            theme provider + tabs
  index.tsx              My List      (filled in by P1-12)
  next-door.tsx          Next Door    (P2-09)
  progress.tsx           Progress     (P1-13)
  menu.tsx               Menu         (P1-03)
src/components/          shared components
src/config/variants.ts   variant resolution (pure, unit-tested)
src/constants/theme.ts   colours, fonts, spacing
src/hooks/
__tests__/               tests that are not next to a single source file
```
