ALTER TABLE boe_contacts
  ADD COLUMN IF NOT EXISTS full_name text
    GENERATED ALWAYS AS (name_first || ' ' || name_last) STORED;

CREATE INDEX IF NOT EXISTS idx_boe_contacts_full_name_zip
  ON boe_contacts(full_name, res_zip);
