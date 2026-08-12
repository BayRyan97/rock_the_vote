-- donations_by_party_year_mv: year x party breakdown, powers the donations
-- dashboard's trend chart (replaces the flat donations_by_party_mv on that
-- page -- that view stays in place for any other ad hoc use, this one adds
-- the year dimension). Same perf rationale as 015_perf_materialized_views.sql:
-- avoids a full-scan join against people on every page load.
-- Refresh after any donation data load:
--   REFRESH MATERIALIZED VIEW CONCURRENTLY donations_by_party_year_mv;
CREATE MATERIALIZED VIEW IF NOT EXISTS donations_by_party_year_mv AS
SELECT EXTRACT(YEAR FROM d.donation_date)::int AS year,
       p.party,
       COUNT(DISTINCT d.donor_key)        AS donors,
       COALESCE(SUM(d.amount::float8), 0) AS total
FROM donations d
JOIN people p USING (donor_key)
WHERE d.confirmed = TRUE AND p.party IS NOT NULL AND d.donation_date IS NOT NULL
GROUP BY 1, 2;

CREATE UNIQUE INDEX IF NOT EXISTS donations_by_party_year_mv_year_party_idx
  ON donations_by_party_year_mv (year, party);
