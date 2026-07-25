-- Indexes for AI targeting query performance

-- Filter columns on people
CREATE INDEX IF NOT EXISTS idx_people_party          ON people (party);
CREATE INDEX IF NOT EXISTS idx_people_age            ON people (age);
CREATE INDEX IF NOT EXISTS idx_people_tier_letter    ON people (tier_letter);
CREATE INDEX IF NOT EXISTS idx_people_turnout_prob   ON people (turnout_prob);
CREATE INDEX IF NOT EXISTS idx_people_dem_lean_prob  ON people (dem_lean_prob);
CREATE INDEX IF NOT EXISTS idx_people_rep_lean_prob  ON people (rep_lean_prob);
CREATE INDEX IF NOT EXISTS idx_people_donor_key      ON people (donor_key);

-- City filter on households (case-insensitive via expression index)
CREATE INDEX IF NOT EXISTS idx_households_city_upper ON households (UPPER(city));
CREATE INDEX IF NOT EXISTS idx_households_county     ON households (county);

-- Donation join
CREATE INDEX IF NOT EXISTS idx_donations_donor_key   ON donations (donor_key);

-- Pre-aggregated donation totals — avoids full-scan CTE on every targeting query
CREATE TABLE IF NOT EXISTS donation_summaries (
    donor_key       text PRIMARY KEY,
    total_donated   numeric NOT NULL DEFAULT 0,
    donation_count  integer NOT NULL DEFAULT 0
);

INSERT INTO donation_summaries (donor_key, total_donated, donation_count)
SELECT
    donor_key,
    COALESCE(SUM(amount), 0) AS total_donated,
    COUNT(*)     AS donation_count
FROM donations
WHERE donor_key IS NOT NULL
GROUP BY donor_key
ON CONFLICT (donor_key) DO UPDATE
    SET total_donated  = EXCLUDED.total_donated,
        donation_count = EXCLUDED.donation_count;
