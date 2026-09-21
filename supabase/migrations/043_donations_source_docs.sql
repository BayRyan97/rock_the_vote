-- Salvage from the abandoned model-donation-v1 branch's 023_donation_sources.sql
-- (archived at tag archive/model-donation-v1).
--
-- That migration's main body -- DROP CONSTRAINT donations_source_check / ADD a
-- three-value one -- is NOT carried over: migration 023_donations_nyccfb_source
-- already did it, and did it better. 001 declared the CHECK inline and unnamed,
-- so its real name is whatever Postgres auto-generated; 023 looks the name up in
-- pg_constraint instead of assuming it, which the branch version did not.
--
-- What is worth keeping from it is the documentation and the index.
--
-- NOTE ON THE INDEX: this is a plain CREATE INDEX, which takes an ACCESS
-- EXCLUSIVE lock on donations for the duration of the build (~3.7M rows). It
-- cannot be made CONCURRENTLY here because Supabase applies each migration
-- inside a transaction and CREATE INDEX CONCURRENTLY is not allowed in one.
-- Apply this at low traffic, and check pg_stat_activity for long-running
-- transactions first -- this instance has previously been starved by stuck
-- week-old transactions. If that risk is unwelcome, drop this file and run the
-- CREATE INDEX CONCURRENTLY by hand outside a transaction instead; nothing in
-- the app depends on the index existing, it only makes source grouping cheaper.

COMMENT ON COLUMN donations.source IS
  'fec = FEC Schedule A (federal, build/fetch_fec_bulk.py). '
  'nyboe = NY State Board of Elections (state/county, build/fetch_nyboe.py). '
  'nyccfb = NYC Campaign Finance Board (city, build/fetch_nyccfb.py). '
  'Each source names the recipient differently: FEC and NY BOE store a '
  'COMMITTEE name, nyccfb stores the CANDIDATE ("Adams, Eric L"). Anything '
  'grouping on committee across sources is grouping on two different things.';

-- Every consumer of per-source totals filters on confirmed, so the partial
-- index is a fraction of the full one and the planner can use it without a
-- recheck. The existing trigram index (007) is on donor_key and serves ILIKE
-- substring search; it does nothing for a GROUP BY source.
CREATE INDEX IF NOT EXISTS idx_donations_source_confirmed
  ON donations (source)
  WHERE confirmed;
