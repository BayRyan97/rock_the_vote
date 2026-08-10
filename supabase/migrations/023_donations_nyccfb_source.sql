-- Adds NYC Campaign Finance Board as a third donation source, so Long Island
-- donors who give to NYC candidates (mayor, comptroller, public advocate,
-- borough president, city council) stop being invisible -- NY State BOE only
-- covers state-level races. See build/fetch_nyccfb.py.
--
-- The source CHECK constraint from 001_initial_schema.sql was declared
-- inline (CHECK (source IN ('fec','nyboe'))) without an explicit name, so
-- its actual name depends on Postgres's auto-naming -- look it up rather
-- than assume, then replace it with an explicitly named one.

DO $$
DECLARE
  cname text;
BEGIN
  SELECT con.conname INTO cname
  FROM pg_constraint con
  JOIN pg_class rel ON rel.oid = con.conrelid
  WHERE rel.relname = 'donations'
    AND con.contype = 'c'
    AND pg_get_constraintdef(con.oid) ILIKE '%source%';

  IF cname IS NOT NULL THEN
    EXECUTE format('ALTER TABLE donations DROP CONSTRAINT %I', cname);
  END IF;
END $$;

ALTER TABLE donations
  ADD CONSTRAINT donations_source_check CHECK (source IN ('fec', 'nyboe', 'nyccfb'));
