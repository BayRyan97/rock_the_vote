-- donation_duplicate_groups: answers "how do we confirm there are no
-- duplicate donations" on demand. A plain (non-materialized) view -- this is
-- an audit tool run occasionally, not a hot dashboard path, and staleness
-- would defeat its purpose.
--
-- Two donations count as the same underlying contribution if they share a
-- donor, a date, and an amount -- regardless of source or committee text.
-- Verified against real data before adding this: NYC CFB and NY State BOE
-- both disclose independent-expenditure contributions, but CFB records the
-- committee as "Diaz Jr., Ruben" where BOE records "Nyc Diaz" for the exact
-- same gift -- committee/source text is NOT a reliable dedup key, donor+date
-- +amount is.
--
-- Usage:
--   SELECT COUNT(*) AS groups, SUM(extra_rows) AS extra_rows,
--          SUM(extra_dollars) AS extra_dollars
--   FROM donation_duplicate_groups;
--
--   SELECT * FROM donation_duplicate_groups ORDER BY extra_dollars DESC LIMIT 50;
CREATE OR REPLACE VIEW donation_duplicate_groups AS
SELECT
  donor_key,
  donation_date,
  amount,
  COUNT(*)                                          AS n_rows,
  COUNT(*) - 1                                       AS extra_rows,
  (COUNT(*) - 1) * amount                            AS extra_dollars,
  array_agg(DISTINCT source ORDER BY source)         AS sources,
  array_agg(DISTINCT committee ORDER BY committee)   AS committees,
  array_agg(id ORDER BY created_at)                  AS ids
FROM donations
WHERE confirmed = TRUE
GROUP BY donor_key, donation_date, amount
HAVING COUNT(*) > 1;
