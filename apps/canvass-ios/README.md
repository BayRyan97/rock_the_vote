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

### Adding a dependency: always reinstall clean

After **any** `npx expo install` or `npm install <pkg>` on the dev machine, before
committing:

```bash
rm -rf node_modules package-lock.json && npm install
```

An incremental install here produces a lock file that works on this machine and
fails `npm ci` everywhere else -- CI on Linux, EAS Build on macOS. It has now
broken a build and a CI run.

The chain: `eslint-config-expo` -> `unrs-resolver`, which has no native
win32-arm64 binding and falls back to `@unrs/resolver-binding-wasm32-wasi` ->
`@napi-rs/wasm-runtime`. That last package declares `@emnapi/core` and
`@emnapi/runtime` as **peerDependencies** with an open range (`^1.7.1`), and the
wasm binding is installed here but skipped on Linux and macOS, where a native
binding exists. The two platforms therefore resolve those peers to different
versions, and a lock file can only record one.

`package.json` pins both in `overrides` so every platform resolves identically.
If `npm ci` starts failing on `@emnapi/*` again, check whether
`@napi-rs/wasm-runtime` has widened its peer range rather than simply deleting
the pin. Note that `npm install --package-lock-only` does **not** repair this --
it reconciles against the existing lock file instead of re-resolving.

## Variants

Three build variants, selected by `APP_VARIANT`, resolved in
[`src/config/variants.ts`](src/config/variants.ts):

| `APP_VARIANT` | Name | Bundle identifier | Backend |
|---|---|---|---|
| `dev` (default) | Bellwether Dev | `org.bellwether.canvass.dev` | `http://localhost:3000` unless overridden |
| `preview` | Bellwether Pre | `org.bellwether.canvass.preview` | `EXPO_PUBLIC_API_URL`, required |
| `production` | Bellwether | `org.bellwether.canvass` | `EXPO_PUBLIC_API_URL`, required |

**"Backend" here is the Bellwether Next.js app in the repo root** — the same API
that already serves the dashboard (`app/api/`), not a second service to stand
up. So a development build needs nothing hosted: `dev` points at `npm run dev`
on the host machine, reached over the LAN. Preview and production need a
deployed origin only because a canvasser working a turf is on cellular, nowhere
near that machine.

Distinct bundle IDs mean a dev build and a TestFlight build can sit on the same
phone without overwriting each other. `resolveVariant` throws on an
unrecognised `APP_VARIANT`, and on a missing API URL for preview or production,
rather than falling back — a preview build silently pointed at `localhost`
fails on the phone hours later and looks like a network bug.

> The `org.bellwether.canvass` prefix was **settled on 2026-09-21** and should
> now be registered as-is under the individual Apple Developer account (task
> S-01). Changing it after the first TestFlight build means a new app record in
> App Store Connect, so treat it as fixed from P1-02 onward. The `.canvass`
> segment leaves room for other Bellwether apps under the same prefix.

## Environment variables

| Variable | Where it is set |
|---|---|
| `APP_VARIANT` | `eas.json` per build profile; unset locally means `dev` |
| `EXPO_PUBLIC_API_URL` | EAS environment (`eas env:set`) for preview/production; a shell variable or `.env.local` for local dev |

`EXPO_PUBLIC_API_URL` is not needed for the first development build (P1-02):
the `dev` variant carries its own default, so `resolveVariant` does not throw.
It is required before the first **preview** build — and there is nothing to
point it at until `POST /api/canvass/events` exists (task P1-08).

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

**Run every `eas` command from this directory**, never from the repo root or a
worktree root. The root is the Next.js app; EAS finds its `package.json`,
assumes that is the Expo project, and silently creates a *second* EAS project
plus a stray root `app.json` pointing at it. That happened on 2026-09-22 with
`eas device:create` — the junk project and the file both had to be deleted. The
device registration survived it, because Apple devices register against the
Apple team, not against an EAS project.

Each profile also sets `environment`, binding it to one of EAS's server-side
environments; that binding is what makes `eas env:set` values visible to a
build. **The names deliberately do not line up:** the build profile is `dev`,
but the EAS environment it reads is `development`, because EAS only ever has
`development`, `preview` and `production`. Expo's guidance is to set the field
explicitly, "to ensure that the correct environment variables are always used",
rather than leaning on a default.

| Build profile | EAS environment | Set a variable with |
|---|---|---|
| `dev` | `development` | `eas env:set development --name … --value …` |
| `preview` | `preview` | `eas env:set preview --name … --value …` |
| `production` | `production` | `eas env:set production --name … --value …` |

`channel` on the preview and production profiles has no effect until
`expo-updates` is installed (spec §14a wants EAS Update for mid-shift fixes).
Harmless, but EAS warns about it on those builds.

The app is linked to the EAS project
**[@thebellwether/thebellwether](https://expo.dev/accounts/thebellwether/projects/thebellwether)**
(`9902ad20-4faf-4229-a5bd-2d7cc86cbb3c`), in the shared `thebellwether`
organization account.

Three fields in `app.config.ts` carry that link and have to stay in sync with
the EAS project — `owner`, `slug`, and `extra.eas.projectId`. They are written
by hand because `eas init` can only edit a static `app.json`, not a dynamic
config. `slug` is `thebellwether` to match the EAS project; it is unrelated to
the iOS bundle identifier, which is per-variant. Verify the link with:

```bash
eas project:info
```

Note this is separate from Apple: the EAS organization account has no bearing
on the Apple Developer account, which is an **individual** enrollment (spec
§14a), nor on the bundle-ID prefix.

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
