-- Town filter on households (case-insensitive via expression index), same
-- pattern as idx_households_city_upper — backs the Turf Search "Town" scope.
CREATE INDEX IF NOT EXISTS idx_households_town_upper ON households (UPPER(town));
