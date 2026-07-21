CREATE TABLE election_results (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  chamber          text NOT NULL CHECK (chamber IN ('assembly','senate','congressional')),
  year             smallint NOT NULL,
  district         text NOT NULL,
  dem_votes        int,
  rep_votes        int,
  other_votes      int,
  total_votes      int,
  dem_candidate    text,
  rep_candidate    text,
  dem_pct          numeric(5,2),
  margin_pct       numeric(5,2),
  winner           text CHECK (winner IN ('DEM','REP','OTHER')),
  UNIQUE (chamber, year, district)
);

CREATE INDEX idx_election_results_chamber_year ON election_results(chamber, year);

ALTER TABLE election_results ENABLE ROW LEVEL SECURITY;

CREATE POLICY "authenticated_read_election_results"
  ON election_results FOR SELECT
  USING (auth.role() = 'authenticated');

CREATE POLICY "admins_write_election_results"
  ON election_results FOR ALL
  USING (get_user_role() = 'admin')
  WITH CHECK (get_user_role() = 'admin');
