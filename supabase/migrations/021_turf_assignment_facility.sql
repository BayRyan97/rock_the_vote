-- turf_assignment rows for voters who live in a facility, not on a walk turf.
--
-- Migration 020 pulled apartment buildings out of the walk list, but their
-- residents are still targets -- they are reached by phone/lobby/relational
-- organising instead of a knock, and the per-voter mass is exactly what says
-- who to call first inside a 652-voter building. Dropping those rows would
-- throw that away; keeping them with the build-time sentinel turf_id = -2
-- violates turf_assignment_turf_id_fkey, which is how this was caught.
--
-- So a row now belongs to exactly one track: a turf OR a facility, never both
-- and never neither. The CHECK is what makes that an invariant the database
-- enforces rather than a convention write_supabase.py has to remember.

ALTER TABLE turf_assignment
  ALTER COLUMN turf_id DROP NOT NULL,
  ADD COLUMN facility_id integer REFERENCES facilities(facility_id) ON DELETE CASCADE,
  ADD CONSTRAINT turf_assignment_one_track
    CHECK ((turf_id IS NULL) <> (facility_id IS NULL));

CREATE INDEX idx_turf_assignment_facility_id ON turf_assignment (facility_id)
  WHERE facility_id IS NOT NULL;
