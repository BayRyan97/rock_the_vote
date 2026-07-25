-- ACS census tract attributes on households
-- Populated by build/fetch_acs.py via spatial point-in-polygon join against
-- TIGER 2023 tract boundaries, then ACS 2019-2023 5-year estimates.

ALTER TABLE households
    ADD COLUMN IF NOT EXISTS acs_tract_geoid   text,
    ADD COLUMN IF NOT EXISTS acs_median_income integer,
    ADD COLUMN IF NOT EXISTS acs_pct_college   numeric(5,2),
    ADD COLUMN IF NOT EXISTS acs_pct_owner_occ numeric(5,2),
    ADD COLUMN IF NOT EXISTS acs_pct_hispanic  numeric(5,2),
    ADD COLUMN IF NOT EXISTS acs_pct_black     numeric(5,2);

CREATE INDEX IF NOT EXISTS idx_households_acs_tract ON households (acs_tract_geoid);
