-- Objective #2, Phase 1: canvass turfs (model/turfs/turfs.py)
-- Ranked, valued canvass turfs built on top of the served scores. Not an
-- update to people/households/donations -- a new targeting layer.
--
-- turf_id is NOT a stable identity across runs: it's a dense integer
-- assigned during Hilbert-sort at build time, so every run is a full
-- snapshot (model/turfs/write_supabase.py TRUNCATEs and reloads both tables
-- together rather than upserting against numbering that can shift).

CREATE TABLE turfs (
  turf_id             integer PRIMARY KEY,
  n_doors             integer NOT NULL,
  n_targets           integer NOT NULL,
  value_dem_ballots   double precision NOT NULL,
  diameter_m          double precision,
  doors_per_km        double precision,
  canvasser_hours     double precision NOT NULL,
  hours_per_ballot    double precision,
  county              text,
  ed_keys_touched     text[],
  is_facility_share   double precision NOT NULL,
  arm                 text NOT NULL CHECK (arm IN ('treatment','control','buffer')),
  model               text NOT NULL,
  computed_at         timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_turfs_value ON turfs (value_dem_ballots DESC);
CREATE INDEX idx_turfs_arm   ON turfs (arm);

-- One row per target voter. person_id joins back to people(id) -- PII-adjacent,
-- unlike turfs itself.
CREATE TABLE turf_assignment (
  person_id  uuid NOT NULL REFERENCES people(id) ON DELETE CASCADE,
  hh_id      integer NOT NULL,
  addr_id    integer NOT NULL,
  turf_id    integer NOT NULL REFERENCES turfs(turf_id) ON DELETE CASCADE,
  m_i        double precision NOT NULL
);

CREATE INDEX idx_turf_assignment_turf_id   ON turf_assignment (turf_id);
CREATE INDEX idx_turf_assignment_person_id ON turf_assignment (person_id);

-- Matches the read/write split already in place for households/people/
-- donations/ev_scores (migration 001, tightened in 016): any authenticated
-- user can read, only admins can write.
ALTER TABLE turfs ENABLE ROW LEVEL SECURITY;
ALTER TABLE turf_assignment ENABLE ROW LEVEL SECURITY;

CREATE POLICY "authenticated_read_turfs"
  ON turfs FOR SELECT
  USING ((select auth.role()) = 'authenticated');

CREATE POLICY "admins_write_turfs"
  ON turfs FOR ALL
  USING ((select get_user_role()) = 'admin')
  WITH CHECK ((select get_user_role()) = 'admin');

CREATE POLICY "authenticated_read_turf_assignment"
  ON turf_assignment FOR SELECT
  USING ((select auth.role()) = 'authenticated');

CREATE POLICY "admins_write_turf_assignment"
  ON turf_assignment FOR ALL
  USING ((select get_user_role()) = 'admin')
  WITH CHECK ((select get_user_role()) = 'admin');
