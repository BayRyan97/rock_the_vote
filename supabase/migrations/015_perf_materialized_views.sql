-- donations_by_party_mv: eliminates 6.8M-block full-scan join (87% cache miss)
-- Refresh after any donation data load: REFRESH MATERIALIZED VIEW CONCURRENTLY donations_by_party_mv;
CREATE MATERIALIZED VIEW IF NOT EXISTS donations_by_party_mv AS
SELECT p.party,
       COUNT(DISTINCT d.donor_key)        AS donors,
       COALESCE(SUM(d.amount::float8), 0) AS total,
       AVG(d.amount::float8)              AS avg
FROM donations d
JOIN people p USING (donor_key)
WHERE d.confirmed = TRUE AND p.party IS NOT NULL
GROUP BY p.party
ORDER BY COALESCE(SUM(d.amount::float8), 0) DESC;

CREATE UNIQUE INDEX IF NOT EXISTS donations_by_party_mv_party_idx
  ON donations_by_party_mv (party);

-- people_party_counts_mv: eliminates 3M-block full-table count scan (69% cache miss)
-- Refresh after any people data load: REFRESH MATERIALIZED VIEW people_party_counts_mv;
CREATE MATERIALIZED VIEW IF NOT EXISTS people_party_counts_mv AS
SELECT
  COUNT(*) FILTER (WHERE party = 'DEM')                    AS dem,
  COUNT(*) FILTER (WHERE party = 'REP')                    AS rep,
  COUNT(*) FILTER (WHERE party = 'BLK')                    AS blk,
  COUNT(*) FILTER (WHERE party NOT IN ('DEM','REP','BLK')) AS other,
  COUNT(*)                                                  AS voters
FROM people;

-- Covering index for DISTINCT assembly_district + score_total filter (68% cache miss)
CREATE INDEX IF NOT EXISTS idx_households_district_score
  ON households (assembly_district, score_total)
  WHERE assembly_district IS NOT NULL;

-- Covering index for DISTINCT city + score_total filter
CREATE INDEX IF NOT EXISTS idx_households_city_score
  ON households (city, score_total)
  WHERE city IS NOT NULL;
