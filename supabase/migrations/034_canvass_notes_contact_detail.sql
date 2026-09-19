-- Interaction detail: who at the household was actually spoken to, what
-- language the conversation happened in, and whether literature was left at
-- the door when nobody could be reached (or in addition to a conversation).
-- Same additive pattern as 031 (mail_ballot_assistance): nullable columns,
-- CHECK constraint widened to accept any of them as "the row isn't blank".

ALTER TABLE canvass_notes ADD COLUMN contact_name text;
ALTER TABLE canvass_notes ADD COLUMN language_spoken text;
ALTER TABLE canvass_notes ADD COLUMN left_pamphlet boolean;

ALTER TABLE canvass_notes DROP CONSTRAINT canvass_notes_check;
ALTER TABLE canvass_notes ADD CONSTRAINT canvass_notes_check CHECK (
  outcome IS NOT NULL OR support_level IS NOT NULL OR issues IS NOT NULL OR
  follow_up_needed IS NOT NULL OR donation_amount IS NOT NULL OR notes IS NOT NULL OR
  mail_ballot_assistance IS NOT NULL OR contact_name IS NOT NULL OR
  language_spoken IS NOT NULL OR left_pamphlet IS NOT NULL
);
