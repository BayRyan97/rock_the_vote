-- Turf toggle on the Canvass Map: households.turf_id
--
-- No foreign key to turfs(turf_id). model/turfs/write_supabase.py TRUNCATEs
-- and reloads `turfs`/`turf_assignment` every run -- turf_id is a dense
-- integer reassigned by Hilbert-sort order each time, not a stable identity
-- across reruns. Postgres refuses to TRUNCATE a table another table's FK
-- references unless the referencing table is truncated in the same statement
-- or CASCADE is used, and CASCADE from turfs would truncate households
-- itself. The relationship is kept in lockstep by application code instead
-- (write_supabase.py resets this column to NULL and repopulates it in the
-- same transaction as the turfs/turf_assignment reload).

ALTER TABLE households ADD COLUMN turf_id integer;

CREATE INDEX idx_households_turf_score ON households (turf_id, score_total)
  WHERE turf_id IS NOT NULL;
