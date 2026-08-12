-- One-time cleanup of duplicate CONFIRMED donations, plus a real constraint
-- so the exact-same-source case can't silently reinsert.
--
-- "Duplicate" here means: same donor_key + donation_date + amount, appearing
-- more than once -- EXCLUDING the case of one source reporting the same
-- donor/date/amount to genuinely different committees (same_source AND
-- multiple distinct committees). That excluded case is dominated by ActBlue/
-- WinRed being recorded as their own "committee" alongside the actual
-- recipient for the same processed gift (~$11.1M) -- both rows are real FEC
-- filings, not an error, and were deliberately left alone (2026-08-11
-- decision). See donation_duplicate_groups (027) for the full breakdown.
--
-- Covers three verified mechanisms:
--   1. Same source, identical row repeated (load artifact / re-run).
--   2. Two sources, exact same committee text, same gift.
--   3. Two sources, same gift, different committee text -- e.g. NYC CFB
--      records "Diaz Jr., Ruben" where NY State BOE records "Nyc Diaz" for
--      the same underlying contribution. Verified by hand across dozens of
--      examples before trusting donor+date+amount as the dedup key.
--
-- For each duplicate group: keep one row (preferring NYBOE's naming, then
-- FEC, then whichever already has employer/occupation filled in), backfill
-- employer/occupation onto the survivor from any sibling that had it, then
-- delete the rest. Nothing outside `confirmed = TRUE` rows is touched.

BEGIN;

-- Pass A: exact (donor_key, source, donation_date, amount, committee)
-- repeats. Always safe regardless of what else shares that donor+date+amount
-- -- these rows are identical in every column that matters, so a group that
-- otherwise contains genuinely different committees (bucket 4, left alone
-- below) can still have an exact repeat hiding inside it, e.g. the same
-- WinRed-side row inserted twice. Caught 42,425 groups / 83,237 rows on
-- 2026-08-11 that the coarser donor+date+amount grouping in Pass B missed
-- for exactly this reason -- discovered when Pass B's unique index creation
-- failed on leftover collisions.
CREATE TEMP TABLE dedup_plan_a AS
SELECT
  (array_agg(id ORDER BY
     (employer IS NOT NULL AND occupation IS NOT NULL) DESC,
     created_at ASC,
     id ASC
  ))[1] AS keep_id,
  array_agg(id) AS all_ids,
  (array_agg(employer ORDER BY id) FILTER (WHERE employer IS NOT NULL))[1]     AS best_employer,
  (array_agg(occupation ORDER BY id) FILTER (WHERE occupation IS NOT NULL))[1] AS best_occupation
FROM donations
WHERE confirmed = TRUE
GROUP BY donor_key, source, donation_date, amount, committee
HAVING COUNT(*) > 1;

UPDATE donations d
SET employer   = COALESCE(d.employer, p.best_employer),
    occupation = COALESCE(d.occupation, p.best_occupation)
FROM dedup_plan_a p
WHERE d.id = p.keep_id;

DELETE FROM donations d
USING dedup_plan_a p
WHERE d.id = ANY(p.all_ids) AND d.id <> p.keep_id;

-- Pass B: remaining (donor_key, donation_date, amount) duplicates across
-- sources / committee spellings (buckets 2 and 3). Runs after Pass A so a
-- bucket-4 group (same source, genuinely different committees -- dominated
-- by ActBlue/WinRed being recorded alongside the real recipient, left alone
-- per 2026-08-11 decision) has no leftover exact repeats to collide on the
-- index created below.
CREATE TEMP TABLE dedup_plan_b AS
SELECT
  (array_agg(id ORDER BY
     (source = 'nyboe') DESC,
     (source = 'fec') DESC,
     (employer IS NOT NULL AND occupation IS NOT NULL) DESC,
     created_at ASC,
     id ASC
  ))[1] AS keep_id,
  array_agg(id) AS all_ids,
  (array_agg(employer ORDER BY id) FILTER (WHERE employer IS NOT NULL))[1]     AS best_employer,
  (array_agg(occupation ORDER BY id) FILTER (WHERE occupation IS NOT NULL))[1] AS best_occupation
FROM donations
WHERE confirmed = TRUE
GROUP BY donor_key, donation_date, amount
HAVING COUNT(*) > 1
   AND NOT (COUNT(DISTINCT source) = 1 AND COUNT(DISTINCT committee) > 1);

UPDATE donations d
SET employer   = COALESCE(d.employer, p.best_employer),
    occupation = COALESCE(d.occupation, p.best_occupation)
FROM dedup_plan_b p
WHERE d.id = p.keep_id;

DELETE FROM donations d
USING dedup_plan_b p
WHERE d.id = ANY(p.all_ids) AND d.id <> p.keep_id;

-- Prevents the same source from ever re-inserting a byte-identical
-- confirmed row again (mechanism #1). Cross-source dedup (#2/#3) can't be
-- expressed as a column-equality constraint since committee/source text
-- legitimately differs -- that's handled by a post-load cleanup pass in
-- build/migrate_donations_psycopg2.py instead.
CREATE UNIQUE INDEX IF NOT EXISTS donations_confirmed_dedup_idx
  ON donations (donor_key, source, donation_date, amount, committee)
  WHERE confirmed = TRUE;

COMMIT;
