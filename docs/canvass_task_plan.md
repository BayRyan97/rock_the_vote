# Canvasser iOS App — Task Plan for Claude Code Sessions (v2)

**Source of truth:** `docs/canvassing_mapping_target.md` (spec) and `CLAUDE.md` (rules).
**Platform:** iOS only (Expo). **Dev and test use real voter data.** Claude Code works directly in the dev and preview environments.

## How to use this plan

- **One task = one Claude Code session = one branch = one PR.** Each task is sized to touch fewer than about 10 files and finish with passing tests.
- **Who:**
  - 🤖 = Claude Code session.
  - 👤 = Tim only: accounts, physical devices, approvals.
  - 🤖👤 = Claude builds, then Tim verifies on device or makes the call.
- **🔍 = needs Tim's line-by-line review** before merge: auth, row-level security, lease SQL, encryption and wipe, voter-field endpoints.
- **Order:** tasks list their dependencies. Tasks with no dependency between them can run in parallel sessions on separate branches.
- **Done means:** the acceptance checks pass, the tests are named in the PR, and any spec deviation is written up.
- **Status column:** ✅ complete · 🟡 in progress · ⬜ incomplete. Update it in the same PR as the work.

### Session prompt template

```
Task <ID>: <title>
Read CLAUDE.md and spec §<sections>. Work on branch task/<ID>.
Goal: <goal>
Deliverables: <list>
Acceptance: <checks>
Write tests first for: <risky logic>. Run tests + lint before finishing and report results.
```

---

## Phase S — Setup (start 👤 items today)

| ID | Status | Who | Task | Depends | Acceptance |
|---|---|---|---|---|---|
| S-01 | ✅ | 👤 | Enroll in the **Apple Developer Program as an individual** (sole developer). **No D-U-N-S number:** Apple requires one only from companies and educational institutions. | — | Enrollment approved. Apple publishes no approval SLA, and it still gates P1-02, so start it early. |
| S-02 | ✅ | 👤 | Create an Expo account, pick an EAS plan, install Node LTS, Git and Claude Code | — | `claude doctor` passes |
| S-03 | ✅ | 👤 | Move the spec into `docs/`, commit `CLAUDE.md`, resolve its TODO paths | — | Merged |
| S-04 | 🟡 | 🤖 | **Dev and preview environments:** Supabase dev and preview projects, loader script that populates them from the real voter data, and `.env.local` wiring for Next.js and `model/` | S-03 | `npm run dev` shows real turf data from the dev project |
| S-05 | ✅ | 🤖 | **CI (GitHub Actions):** lint, type-check, Jest, and Python tests on every PR. Also a **secret scan and a data-file blocker** (`*.csv`, `*.parquet`, `.env*`) because the repo is public on GitHub. | S-03 | A PR containing a planted `.env` or CSV fails CI |

**Phase S gate:** Claude can build, run and test end to end against the dev environment.

---

## Phase 0 — Data foundation and utility layer (spec §5, §6, §12.2, §12.4)

| ID | Who | Task | Depends | Acceptance |
|---|---|---|---|---|
| P0-01 | 🤖 🔍 | **Entity model migration:** `buildings`, `units`, households→units link, building `type`, `access_notes`, PostGIS points with GiST indexes | S-04 | Migration runs on dev. Rollback works. |
| P0-02 | 🤖 | **Building-type classifier** from address parsing (unit designators), with a `needs_review` flag when more than 10 voters share one household key. Never infer type from voter count alone. | P0-01 | Unit tests on tricky addresses (Apt, Unit, #, rear, basement). Review-queue count on dev data. |
| P0-03 | 🤖 | **Load household points:** load TIGER geocode-cache points into `buildings.geom` | P0-01 | Load count and unresolved count reported |
| P0-04 | 🤖 | **Utility config + `utility.py`:** versioned YAML (`gotv_broad_v4`, `gotv_v3`). Per-voter eligibility (lean ≥ 0.30), turnout taper with a 25% floor, unit cap of 10, `utility_version` stamping. | S-03 | Unit tests reproduce the spec §6 examples (the v3 vs v4 table) |
| P0-05 | 🤖 | **`stop_scores` build job:** aggregate eligible voters per stop, precomputed on score refresh | P0-04, P0-01 | Values match `utility.py`. Full-county runtime reported. |
| P0-06 | 🤖 | **Calibration check** (§12.2): curves for lean in the 0.25–0.40 band and for turnout across its full range, split by county and BLK vs. registered | S-04 | Report produced. Tim decides whether the 0.30 floor stays. |
| P0-07 | 🤖👤 | **Geocode QA tool** (§12.4): sample stratification, error metrics (median, P90/P95, wrong segment, across barrier, unresolved). Claude builds it and computes the metrics. **Tim spot-checks the ~100-point sample on satellite imagery.** | P0-03 | Metrics report. Pass or fail recorded. |
| P0-08 | 🤖👤 | **Pool check at lean floors 0.40, 0.35, 0.30 and 0.25** on 2–3 turfs, including the unaffiliated share of added doors. Claude runs it, Tim decides. | P0-05 | Decision #2 confirmed or adjusted |

**Phase 0 gate:** stop scores exist for real turfs, geocode QA has passed or has a remediation plan, and the lean floor is confirmed.

---

## Phase 1 — iOS app shell and offline store (spec §4, §9, §11, §14a)

| ID | Who | Task | Depends | Acceptance |
|---|---|---|---|---|
| P1-01 | 🤖 | **Expo project scaffold** in `apps/canvass-ios/`: `expo-dev-client`, TypeScript, `app.config.ts` with the `APP_VARIANT` dev/preview/production bundle IDs, `eas.json` profiles, navigation tabs (My List, Next Door, Progress, Menu) | S-05 | `npm test` passes. The config resolves three distinct bundle IDs. |
| P1-02 | 👤 | Register the iPhone (`eas device:create`), run the first **development build**, install it | P1-01, S-01 | App opens on the phone with live reload |
| P1-03 | 🤖 🔍 | **Auth:** Supabase auth via the Bellwether API, tokens in `expo-secure-store`, short-lived access tokens, one active device per account, minimum-version check at login | P1-01, S-04 | Tests: expired token, second device, outdated version |
| P1-04 | 🤖 🔍 | **Encrypted local store:** SQLCipher-backed SQLite for the route cache, candidate pool, walk matrix, display fields, and outbox | P1-01 | Test that the DB file is unreadable without the key |
| P1-05 | 🤖 🔍 | **Cache wipe:** wipe at End Shift, after 12 hours idle, at logout, on a remote-wipe flag, and at next launch after an expired shift | P1-04 | Tests for each trigger |
| P1-06 | 🤖 | **Outcome model and rules:** the full outcome set (§4.4), re-knock rules, compounding decay, progression vs. invalidation classes | S-03 | Table-driven tests that mirror the §4.4 table |
| P1-07 | 🤖 | **Outbox + sync client:** `client_event_id`, immediate sync plus a 2–5 minute timer plus on-reconnect, batch post, cursor, and handling of rejected events | P1-04, P1-06 | Tests: duplicate send, offline queue, reject → Attention state |
| P1-08 | 🤖 🔍 | **Events API (server):** `POST /api/canvass/events`, idempotent insert, team deltas since cursor, lease and exclusion changes, derived door-state view with precedence | P0-01 | Tests: idempotency, precedence (Do not contact > Refused > Contacted…), campaign isolation |
| P1-09 | 🤖 | **Sync badge component** with the four states (§4.7) | P1-07 | Component tests for each state |
| P1-10 | 🤖 | **Outcome sheet UI** (MiniVAN-style): per-person and whole-household actions, Come Back time picker, Do not contact escalation | P1-06 | Component tests. Writes go to the outbox. |
| P1-11 | 🤖 | **Door detail UI:** address, first name and last initial, directions link, report a pin problem, Request access. Renders only the fields the API returns. | P1-10 | Test: hidden fields are never rendered |
| P1-12 | 🤖 | **My List map + list:** MapLibre clusters, state-colored pins, filters, walking-order sort | P1-11 | Renders a full real turf smoothly on device (Tim checks) |
| P1-13 | 🤖 | **Progress tab + End Shift flow:** sync, then wipe | P1-05, P1-07 | End Shift leaves no cached voter data (test) |
| P1-14 | 👤 | **Device test on a real turf (dev):** full shift walkthrough, then 30 minutes in airplane mode, then reconnect with zero lost events | P1-02…P1-13 | Checklist signed off |

**Phase 1 gate:** the app works on the iPhone against dev, including offline, sync and wipe.

---

## Phase 2 — Routing service and live state (spec §7, §8, §13)

| ID | Who | Task | Depends | Acceptance |
|---|---|---|---|---|
| P2-01 | 🤖 | **Candidate retrieval:** PostGIS query with an expanding radius. Exclusions: leases, contacted, Do not contact, attempt limits, time gates (Come Back, Day Sleeper), group quarters unless granted. | P0-05 | Tests for each exclusion |
| P2-02 | 🤖 | **Walk-time provider interface** with a haversine × 1.3 implementation. The OSRM adapter comes later. | — | Unit tests. Swappable by config. |
| P2-03 | 🤖 | **Greedy solver + 2-opt + swap/insert moves** in Python, honoring both caps (max doors, time budget) and returning the binding constraint | P2-02 | Tests: caps respected, binding constraint correct, swap move fixes a planted selection mistake |
| P2-04 | 🤖 | **Same solver in TypeScript** for on-device re-planning, with a shared test-vector file used by both implementations | P2-03 | Python and TypeScript produce identical routes on the shared vectors |
| P2-05 | 🤖 | **Interim λ presets:** per-request base rate from the local pool medians, Density 2×, Balanced 1×, Priority 0.5×, logged with each route | P2-03 | Tests: Density walks less than Priority on the same pool |
| P2-06 | 🤖 🔍 | **Lease claim protocol:** a single transaction that takes over only expired leases, re-solves on partial loss (at most 2 retries), logs `partial_claim`, tiered hard/soft leases tied to arrival times | P0-01 | **Concurrency test:** two simulated canvassers at the same time, zero shared hard leases |
| P2-07 | 🤖 | **Route API:** `POST /api/canvass/routes` per §13, with a pinned stability horizon of 3 stops and `utility_version` stamping | P2-01, P2-05, P2-06 | Contract test against the §13 example shape. No scores in the response. |
| P2-08 | 🤖 | **Re-solve state machine (server + client):** progression vs. invalidation triggers, background refresh every 10 doors with a ≥ 5% improvement rule | P2-07, P1-07 | Tests for each trigger in the §8.2 table |
| P2-09 | 🤖 | **Start Route sheet + Next Door timeline UI:** GPS or typed address with a draggable pin, shift and cap sliders, "34 doors fit" preview, teammate toasts, Updated chip | P2-07, P1-12 | Component tests. The preview matches the API. |
| P2-10 | 🤖 | **Geocoding:** Expo `geocodeAsync` first, server Census fallback | P2-09 | Tests with mocked failures |
| P2-11 | 🤖 🔍 | **Access grants:** table, field-director issuance API, Request access flow, retrieval join, unit-cap override, expiry and revocation | P2-01, P1-11 | Tests: grant applies to one canvasser only. Expired grant reverts. |
| P2-12 | 🤖 🔍 | **Field-visibility settings:** age, sex and party toggles by campaign, team or canvasser. Narrowest scope wins. Omitted from API **and** cache. Audited. | P1-08 | Test: a disabled field never leaves the server |
| P2-13 | 🤖 | **Offline re-plan on device** using the TypeScript solver over the cached pool | P2-04, P2-08 | Airplane-mode invalidation re-plans locally (test with a mocked network) |
| P2-14 | 🤖 | **Real-turf routing check** on 2–3 turfs: route sanity, pins, walk times, exclusion counts | P2-07, P0-07 | Findings logged as tasks |
| P2-15 | 👤 | **Two-iPhone overlap test** on a real turf in preview | P2-06, P2-09 | Zero double-knocks |

**Phase 2 gate:** real routes on real turfs, no double-knocks, offline re-plan works.

---

## Phase 3 — Operational experiments and logging (spec §12.1a–b)

| ID | Who | Task | Depends | Acceptance |
|---|---|---|---|---|
| P3-01 | 🤖 | **`experiment_assignments` table + assignment service** (server-side, at retry scheduling or shift start) | P1-08 | Tests: stable assignment, roughly 50/50 split |
| P3-02 | 🤖 | **Retry-timing experiment:** same vs. different time-of-day block for Not Home retries | P3-01, P1-06 | Tests that retries honor the arm |
| P3-03 | 🤖 | **Travel-setting shift experiment** (Density vs. Balanced) | P3-01, P2-05 | Arm logged on each route |
| P3-04 | 🤖 | **Logging completeness check:** every route has its preset, λ, solver and utility version, and every event has an arm where relevant | P3-02, P3-03 | Nightly check job with a failing test when fields are missing |
| P3-05 | 🤖 | **Retention jobs:** strip knock GPS after the cycle, auto-delete copilot audit logs after 24 months | P1-08 | Tests with backdated timestamps |

---

## Phase 4 — Solver refinement and presets (spec §7.1, §7.3–7.5, §12.3)

| ID | Who | Task | Depends | Acceptance |
|---|---|---|---|---|
| P4-01 | 🤖👤 | **Self-hosted OSRM (foot profile, NY extract)** with Docker config and a raised `--max-table-size`. Claude writes the config and adapter. **Tim hosts it.** | P2-02 | `/table` for 151 points returns within budget |
| P4-02 | 🤖 | **Snapping check + unroutable handling** (more than 40 m or across a barrier → flag and exclude) | P4-01 | Tests with planted bad points. Flagged count on real turfs. |
| P4-03 | 🤖 | **OR-Tools asynchronous refinement:** disjunctions, time and count dimensions, open path, 1.5 s limit, replaces the tail only if ≥ 3% better | P2-07 | Never reorders the pinned 3 stops (test). The UI shows an Optimizing chip. |
| P4-04 | 🤖 | **Solver benchmark harness:** v0 vs. v1 vs. a 60 s reference on real turf instances. Reports the gap, selection fixes and runtime. | P4-03 | Report generated |
| P4-05 | 🤖👤 | **λ sensitivity sweep** on 5–10 real turfs: plot the priority vs. doors vs. walking curve. Claude runs it. **Tim picks the presets with the campaign manager.** | P4-01 | Interim λ replaced in config (release gate) |
| P4-06 | 🤖 | **Latency hardening:** p95 time to first list ≤ 2.5 s. Load test against preview. | P4-03 | Load test report |

---

## Phase 5 — Ops dashboard and AI copilot (spec §10)

⚠️ **Confirm the copilot design with Anthropic before P5-04 ships.**

| ID | Who | Task | Depends | Acceptance |
|---|---|---|---|---|
| P5-01 | 🤖 🔍 | **`ops.*` aggregate views + `copilot_ro` Postgres role** with small-cell suppression (fewer than 10 households) | P1-08 | Tests: no ID, name or score columns. Small cells suppressed. The role can't select `people`. |
| P5-02 | 🤖 | **Dashboard pages:** turf progress, team activity, sync health, field issues, access-request queue, field-visibility settings, grants | P5-01 | Renders from dev data |
| P5-03 | 🤖 🔍 | **Bellwether MCP server:** the 10 read-only tools from §10.3, OAuth scoped to one campaign, `campaign_id` injected server-side, audit logging | P5-01 | Tests: cross-campaign argument ignored. Tool outputs contain no identifiers. |
| P5-04 | 🤖 | **Copilot chat panel:** Claude API with the MCP connector (check the current docs for request format), freshness stamps, suggested prompts, proposal buttons (read-only v1), refusal of voter-level questions | P5-03 | Test prompts, including "tell me about the voters at <address>", which must be declined |
| P5-05 | 🤖 | **Canvasser help mode** in the app: `get_help_article` only | P5-03 | No data tools reachable from the app |

---

## Phase R — Release (spec §14a)

| ID | Who | Task | Depends | Acceptance |
|---|---|---|---|---|
| R-01 | 🤖 | **App Store review demo account:** a demo login and a small demo campaign for Apple's reviewers, plus walkthrough notes. Apple requires a working login for review. See the note below on what data it should contain. Because the app ships from an **individual** account, the notes must also pre-empt guideline 5.1.1(ix) by stating the operator relationship explicitly — who runs the campaign, that the developer is authorized by it, and that the app is login-only and not for the general public. | P1-03 | Reviewer can complete a route |
| R-02 | 🤖 | **Privacy labels and store metadata draft** (precise location for app functionality, user IDs), plus the individual-account listing decisions: the developer name will be Tim's legal name and can't be edited after the first app record; a support URL and privacy-policy URL that aren't a personal address; territory availability (excluding the 27 EU territories keeps DSA trader contact details unpublished); and the export-compliance answer, since the local store is SQLCipher-encrypted. | P1-03 | Draft reviewed by Tim |
| R-03 | 🤖 | **Crash reporting (Sentry)** with voter-data scrubbing | P1-01 | Test: an event payload with a planted name is scrubbed |
| R-04 | 👤 | **Preview beta** to field director + 2–3 canvassers, then **TestFlight** pilot | Phases 1–2 | Pilot feedback logged as tasks |
| R-05 | 👤 | **Release gates:** retry-arm logging verified, geocode QA passed, airplane-mode test, two-canvasser test, λ sweep done, store approval | All | Every gate checked |
| R-06 | 👤 | `eas build --profile production --platform ios` → `eas submit` → App Store review (allow 1–2 weeks) | R-05 | Approved |

**Note on R-01:** Apple's reviewers are outside the campaign. The review account should show a small demo campaign with made-up people rather than real voter records. This is about third-party exposure during review, not about how you develop.

---

## Suggested order

**Week 1:** S-01, S-02 and S-03 (👤) · S-04 → S-05 (🤖)
**Then:** Phase 0 and the start of Phase 1 in parallel (P0-01…P0-05 alongside P1-01, P1-04, P1-06)
**Critical path:** S-01 (Apple enrollment) → P1-02 (first device build) → P1-14 → Phase 2 → R-04 → R-06

**Note on S-01 (individual enrollment, decided 2026-09-21).** Organization enrollment could not even *begin* until a D-U-N-S number came back from D&B — Apple documents ceilings of up to 5 business days for D&B to issue it and up to 2 more for Apple to receive it, with an escalation path at two weeks. Individual enrollment skips that step entirely, so the front-loaded Apple risk is gone. S-01 still heads the chain because P1-02 cannot run without it, but the remaining Apple risk now sits at the *end* of the plan, in TestFlight Beta App Review (R-04) and App Store review (R-06). Budget accordingly: pull schedule slack toward the release phase rather than the setup phase.
