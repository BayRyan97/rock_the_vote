-- households.legislative_district — NY county legislature district (Nassau
-- County Legislature / Suffolk County Legislature; local county government,
-- NOT the NYS Assembly/Senate columns above). Present in the raw voter CSVs
-- (data/Nassau.csv, data/Suffolk.csv) but dropped on the way into Supabase:
-- build/migrate_to_supabase.py never read row.get("legislative_district"), so
-- every existing household has it NULL. This migration only adds the column;
-- build/backfill_legislative_district.py (run once, after this migration is
-- applied) fills it in from the local CSVs by re-deriving each row's
-- household id via the same deterministic hash migrate_to_supabase.py uses,
-- with no re-geocoding required. migrate_to_supabase.py itself is updated in
-- the same change so future full re-migrations stop dropping the column.

ALTER TABLE households ADD COLUMN legislative_district smallint;

CREATE INDEX idx_households_legislative_district
  ON households (legislative_district);
