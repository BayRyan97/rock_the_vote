/**
 * Schema for the encrypted local store (task P1-04).
 *
 * Covers exactly what spec §9.2 says is cached at route start -- the planned
 * route, the candidate pool, the walk-time matrix, display fields, the outcome
 * form definitions and the utility version -- plus the §9.3 outbox.
 *
 * Migrations are an ordered, append-only list. `user_version` records how far a
 * device has got. Never edit a shipped migration: a phone that already ran it
 * will not run it again, so an edit only changes the schema on new installs and
 * the two drift apart silently.
 */

/**
 * Columns that must never exist on this device.
 *
 * CLAUDE.md: "never store or render turnout or lean scores". The server
 * decides targeting; the phone gets an ordered list of doors and the fields
 * needed to knock them. `schema.test.ts` enforces this against the DDL, so a
 * later task cannot quietly add a score column to a cache table.
 */
export const FORBIDDEN_COLUMN_PATTERNS = [
  /score/i,
  /turnout/i,
  /\blean\b/i,
  /propensity/i,
  /utility(?!_version)/i,
  /priority/i,
] as const;

/**
 * Ordered migrations. Index + 1 is the `user_version` the migration produces,
 * so migration 0 in this array takes a fresh database to version 1.
 */
export const MIGRATIONS: readonly string[] = [
  // v1 -- the P1-04 cache.
  `
  -- One row. Holds the shift context the whole cache belongs to, so P1-05 can
  -- answer "is this cache stale?" without scanning any voter data.
  CREATE TABLE cache_meta (
    id               INTEGER PRIMARY KEY CHECK (id = 1),
    shift_id         TEXT    NOT NULL,
    canvasser_id     TEXT    NOT NULL,
    campaign_id      TEXT    NOT NULL,
    -- The §7 utility config version the server planned this route with. Kept
    -- so a cached route is never re-planned on device under different rules.
    utility_version  TEXT    NOT NULL,
    cached_at        INTEGER NOT NULL,
    last_active_at   INTEGER NOT NULL
  );

  -- The planned route: an ordered list of stops (§8.1 hard/soft tiers).
  CREATE TABLE route_stops (
    stop_id          TEXT    PRIMARY KEY,
    position         INTEGER NOT NULL,
    lease_tier       TEXT    NOT NULL CHECK (lease_tier IN ('hard', 'soft')),
    lease_expires_at INTEGER,
    planned_arrival  INTEGER,
    visited_at       INTEGER
  );
  CREATE UNIQUE INDEX idx_route_stops_position ON route_stops (position);

  -- The local candidate pool: up to ~300 stops for offline re-planning
  -- (§9.4). Minimal fields by design -- §11 keeps the pool leaner than the
  -- route itself.
  CREATE TABLE candidate_stops (
    stop_id       TEXT    PRIMARY KEY,
    building_id   TEXT    NOT NULL,
    latitude      REAL    NOT NULL,
    longitude     REAL    NOT NULL,
    unit_count    INTEGER NOT NULL DEFAULT 1,
    building_type TEXT,
    access_notes  TEXT,
    excluded_at   INTEGER
  );

  -- Walk-time matrix over the pool. Directed: one row per ordered pair, since
  -- one-way segments and stairs are not symmetric.
  CREATE TABLE walk_matrix (
    from_stop_id  TEXT    NOT NULL REFERENCES candidate_stops (stop_id) ON DELETE CASCADE,
    to_stop_id    TEXT    NOT NULL REFERENCES candidate_stops (stop_id) ON DELETE CASCADE,
    walk_seconds  INTEGER NOT NULL,
    PRIMARY KEY (from_stop_id, to_stop_id)
  ) WITHOUT ROWID;

  -- Display fields for the people at each stop. Which columns the server
  -- actually populates is governed by field visibility (§4.3, task P2-12);
  -- a field turned off server-side arrives NULL and must stay NULL.
  CREATE TABLE stop_people (
    person_id     TEXT    PRIMARY KEY,
    stop_id       TEXT    NOT NULL REFERENCES candidate_stops (stop_id) ON DELETE CASCADE,
    display_name  TEXT    NOT NULL,
    unit_label    TEXT,
    age           INTEGER,
    sex           TEXT,
    party         TEXT,
    do_not_contact INTEGER NOT NULL DEFAULT 0
  );
  CREATE INDEX idx_stop_people_stop ON stop_people (stop_id);

  -- Outcome form definitions (§4.4), cached so the sheet works offline.
  CREATE TABLE outcome_forms (
    outcome_code  TEXT    PRIMARY KEY,
    label         TEXT    NOT NULL,
    definition    TEXT    NOT NULL,
    sort_order    INTEGER NOT NULL
  );

  -- The §9.3 outbox. client_event_id is device-generated and UNIQUE: it is
  -- what makes a retry idempotent end to end, here and at the server.
  CREATE TABLE outbox (
    client_event_id TEXT    PRIMARY KEY,
    stop_id         TEXT    NOT NULL,
    payload         TEXT    NOT NULL,
    created_at      INTEGER NOT NULL,
    attempts        INTEGER NOT NULL DEFAULT 0,
    last_attempt_at INTEGER,
    rejected_reason TEXT
  );
  -- Partial index: the sync client (P1-07) only ever scans unsent events.
  CREATE INDEX idx_outbox_pending ON outbox (created_at) WHERE rejected_reason IS NULL;
  `,
];

/** The `user_version` a fully migrated database reports. */
export const SCHEMA_VERSION = MIGRATIONS.length;

/**
 * Table names the cache is made of, in an order safe to delete in with foreign
 * keys on: children before parents. P1-05's wipe uses this.
 */
export const CACHE_TABLES = [
  'outbox',
  'stop_people',
  'walk_matrix',
  'route_stops',
  'candidate_stops',
  'outcome_forms',
  'cache_meta',
] as const;
