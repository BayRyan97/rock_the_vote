-- Add voter_id to people as a join key to boe_contacts
ALTER TABLE people
  ADD COLUMN IF NOT EXISTS voter_id text;

CREATE UNIQUE INDEX IF NOT EXISTS idx_people_voter_id
  ON people(voter_id) WHERE voter_id IS NOT NULL;
