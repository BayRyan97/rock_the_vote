-- Trigram indexes for fast ILIKE %pattern% search on large tables.
-- Without these, name/address searches do full sequential scans (1.8M+ rows).
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX IF NOT EXISTS idx_people_name_trgm
  ON people USING gin (name gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_households_street_trgm
  ON households USING gin (street gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_households_city_trgm
  ON households USING gin (city gin_trgm_ops);
