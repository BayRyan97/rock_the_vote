-- Snapshot which turf a note was collected in, captured from
-- households.turf_id at INSERT time (app/api/households/[id]/notes POST)
-- rather than joined live -- turf_id is a dense integer reassigned by every
-- model rerun (017/018), so a live join would silently reattribute old
-- notes to whatever turf that number happens to mean today. No FK, matching
-- households.turf_id's own lack of one for the same reason.
ALTER TABLE canvass_notes ADD COLUMN turf_id integer;

CREATE INDEX idx_canvass_notes_turf ON canvass_notes (turf_id)
  WHERE turf_id IS NOT NULL;

-- Pre-existing rows are left NULL, not backfilled from the CURRENT
-- households.turf_id: there is no way to recover which turf was active when
-- an old note was collected, and today's turf_id numbering is not the same
-- identity space as whatever was active historically (017's comment: dense
-- integers reassigned on every rerun). Backfilling would silently mislabel
-- historical notes with a number that means something different now --
-- worse than an honest "unknown." The manager view should render a NULL
-- turf_id as "Unknown turf" rather than blank/zero.
