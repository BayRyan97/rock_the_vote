-- Canvassing notes: household-level field notes logged from the Search page.
-- All fields are optional individually, but a row can't be entirely blank
-- (enforced below), and a donation pledge requires a way to follow up.

CREATE TABLE canvass_notes (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id      uuid NOT NULL REFERENCES households(id) ON DELETE CASCADE,
  canvasser_id      uuid NOT NULL REFERENCES profiles(id),
  created_at        timestamptz NOT NULL DEFAULT now(),

  outcome           text CHECK (outcome IN ('contact','not_home','refused','moved')),
  support_level     text CHECK (support_level IN
                        ('strong_support','lean_support','undecided','lean_oppose','strong_oppose')),
  issues            text,
  follow_up_needed  boolean,

  -- Self-reported field pledge, distinct from the verified donations table
  -- (donations.amount, sourced from FEC/NYBOE/NYCCFB).
  donation_amount   numeric(10,2) CHECK (donation_amount IS NULL OR donation_amount > 0),
  donor_name        text,
  donor_phone       text,
  donor_email       text,

  notes             text,

  CHECK (
    outcome IS NOT NULL OR support_level IS NOT NULL OR issues IS NOT NULL OR
    follow_up_needed IS NOT NULL OR donation_amount IS NOT NULL OR notes IS NOT NULL
  ),
  CHECK (
    donation_amount IS NULL OR
    (donor_name IS NOT NULL AND (donor_phone IS NOT NULL OR donor_email IS NOT NULL))
  )
);

CREATE INDEX idx_canvass_notes_household ON canvass_notes (household_id);
CREATE INDEX idx_canvass_notes_canvasser ON canvass_notes (canvasser_id);

-- Shared read (any authenticated canvasser sees the whole team's notes on a
-- household), own-row write -- same read/write split as turfs (017) and the
-- rest of the voter-data tables (001/002/016).
ALTER TABLE canvass_notes ENABLE ROW LEVEL SECURITY;

CREATE POLICY "authenticated_read_canvass_notes"
  ON canvass_notes FOR SELECT
  USING ((select auth.role()) = 'authenticated');

CREATE POLICY "canvassers_insert_own_notes"
  ON canvass_notes FOR INSERT
  WITH CHECK (canvasser_id = (select auth.uid()));

CREATE POLICY "authors_update_own_notes"
  ON canvass_notes FOR UPDATE
  USING (canvasser_id = (select auth.uid()) OR (select get_user_role()) = 'admin')
  WITH CHECK (canvasser_id = (select auth.uid()) OR (select get_user_role()) = 'admin');

CREATE POLICY "authors_delete_own_notes"
  ON canvass_notes FOR DELETE
  USING (canvasser_id = (select auth.uid()) OR (select get_user_role()) = 'admin');
