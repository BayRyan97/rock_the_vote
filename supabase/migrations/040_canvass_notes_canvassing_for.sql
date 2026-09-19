-- Which candidate's campaign a note was collected for, answered by the
-- canvasser on every note (app/api/households/[id]/notes POST enforces
-- non-blank). NOT NULL outright, not backfilled -- there are zero existing
-- rows (table added in migration 030, still empty), so there's nothing to
-- backfill and no default value is needed.
--
-- This is the field "running" and "campaign_manager" are scoped by in the
-- manager notes browser (see 041 for the matching profiles-read policy, and
-- app/api/notes/route.ts for the scoping logic): running matches on their
-- own profiles.name, campaign_manager matches on their profiles.campaign_name.
ALTER TABLE canvass_notes ADD COLUMN canvassing_for text NOT NULL;

CREATE INDEX idx_canvass_notes_canvassing_for ON canvass_notes (canvassing_for);
