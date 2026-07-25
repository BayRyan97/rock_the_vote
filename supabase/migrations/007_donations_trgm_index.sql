CREATE INDEX IF NOT EXISTS idx_donations_donor_key_trgm
  ON donations USING gin (donor_key gin_trgm_ops);
