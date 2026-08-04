-- Apartment buildings and facilities: a separate tactic track.
--
-- A canvasser cannot knock a locked lobby. Counting a 58-voter building as one
-- 3-minute door made apartment-dense turfs look like the most efficient walk
-- lists in the county -- turf 1443 (Great Neck Plaza) ranked #1 on 151 "doors"
-- of which 39 were buildings holding 896 of its 1,136 targets, with the best
-- hours_per_ballot in Nassau. The density is a real opportunity; door-knocking
-- is just the wrong instrument for it. These rows are held out of the walk
-- list (model/turfs/turfs.py gives them turf_id -2) and ranked here instead,
-- for lobby access, phone, and relational organising.
--
-- No hours figure on purpose: what it costs to reach a building is an
-- organising question, not doors ÷ 20/hour.

CREATE TABLE facilities (
  facility_id       integer PRIMARY KEY,   -- dense per-run id, same snapshot semantics as turf_id
  household_id      uuid NOT NULL REFERENCES households(id) ON DELETE CASCADE,
  n_targets         integer NOT NULL,
  household_size    integer NOT NULL,      -- registered voters at the address; what tripped the flag
  value_dem_ballots double precision NOT NULL,
  value_net_margin  double precision NOT NULL,
  lat               double precision,
  lon               double precision,
  county            text,
  ed_key            text,
  -- The walk turf whose canvassers are already closest, so a field organiser
  -- can hand both to the same team. FK is safe here (unlike households.turf_id
  -- in migration 018) because write_supabase.py TRUNCATEs facilities in the
  -- SAME statement as turfs -- Postgres requires exactly that.
  nearest_turf_id   integer REFERENCES turfs(turf_id) ON DELETE SET NULL,
  model             text NOT NULL,
  computed_at       timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_facilities_value ON facilities (value_net_margin DESC);
CREATE INDEX idx_facilities_turf  ON facilities (nearest_turf_id);

-- Lets the Canvass Map style a building differently from a door. Reset and
-- repopulated every run by write_supabase.py, like households.turf_id.
ALTER TABLE households ADD COLUMN is_facility boolean NOT NULL DEFAULT false;
CREATE INDEX idx_households_is_facility ON households (is_facility) WHERE is_facility;

-- Matches the read/write split already in place for turfs (migration 017):
-- any authenticated user can read, only admins can write.
ALTER TABLE facilities ENABLE ROW LEVEL SECURITY;

CREATE POLICY "authenticated_read_facilities"
  ON facilities FOR SELECT
  USING ((select auth.role()) = 'authenticated');

CREATE POLICY "admins_write_facilities"
  ON facilities FOR ALL
  USING ((select get_user_role()) = 'admin')
  WITH CHECK ((select get_user_role()) = 'admin');
