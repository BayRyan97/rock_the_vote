-- Mail ballot assistance flag: a candidate wants to follow up with
-- households that need help with (or were already helped with) a
-- mail-in ballot. Same nullable-boolean pattern as follow_up_needed.

ALTER TABLE canvass_notes ADD COLUMN mail_ballot_assistance boolean;

ALTER TABLE canvass_notes DROP CONSTRAINT canvass_notes_check;
ALTER TABLE canvass_notes ADD CONSTRAINT canvass_notes_check CHECK (
  outcome IS NOT NULL OR support_level IS NOT NULL OR issues IS NOT NULL OR
  follow_up_needed IS NOT NULL OR donation_amount IS NOT NULL OR notes IS NOT NULL OR
  mail_ballot_assistance IS NOT NULL
);
