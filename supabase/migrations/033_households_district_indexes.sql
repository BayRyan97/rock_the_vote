-- Turf Search / Canvass Map "Narrow by area" is becoming a combinable,
-- multi-dimension filter (county, city, town, election_district,
-- legislative_district, congressional_district, senate_district,
-- assembly_district), each of which now runs both a live DISTINCT-values
-- query (cascading checklists) and an EXISTS/ANY() scoping predicate on
-- every keystroke-debounced request. AD and county already have indexes
-- (idx_households_assembly, idx_households_county); city/town have UPPER()
-- expression indexes (migrations 009, 029). election_district,
-- senate_district, and congressional_district have never had a standalone
-- index — only the composite (county, election_district), whose second
-- column is unusable without an equality predicate on county first.

CREATE INDEX IF NOT EXISTS idx_households_election_district
  ON households (election_district);
CREATE INDEX IF NOT EXISTS idx_households_senate_district
  ON households (senate_district);
CREATE INDEX IF NOT EXISTS idx_households_congressional_district
  ON households (congressional_district);
