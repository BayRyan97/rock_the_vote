CREATE TABLE IF NOT EXISTS boe_contacts (
  voter_id  text PRIMARY KEY,
  email     text,
  phone     text,
  updated_at timestamptz DEFAULT now()
);
