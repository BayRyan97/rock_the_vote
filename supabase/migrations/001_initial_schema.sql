-- Rock the Vote: Initial Schema
-- Voter data for Nassau and Suffolk counties, Long Island NY

CREATE TABLE households (
  id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  county                text NOT NULL CHECK (county IN ('NASSAU','SUFFOLK')),
  address_num           text NOT NULL,
  street                text NOT NULL,
  city                  text NOT NULL,
  zip                   text NOT NULL,
  town                  text,
  election_district     smallint,
  assembly_district     smallint,
  senate_district       smallint,
  congressional_district smallint,
  lon                   numeric(9,5),
  lat                   numeric(9,5),
  score_total           smallint NOT NULL DEFAULT 0,
  score_wake_ups        smallint NOT NULL DEFAULT 0,
  score_unaffiliated    smallint NOT NULL DEFAULT 0,
  score_dropoff         smallint NOT NULL DEFAULT 0,
  ev_score              smallint,
  created_at            timestamptz DEFAULT now(),
  updated_at            timestamptz DEFAULT now()
);

CREATE INDEX idx_households_zip           ON households(zip);
CREATE INDEX idx_households_city          ON households(city);
CREATE INDEX idx_households_county_ed     ON households(county, election_district);
CREATE INDEX idx_households_assembly      ON households(assembly_district);
CREATE INDEX idx_households_score         ON households(score_total DESC);
CREATE INDEX idx_households_geo           ON households(lat, lon)
  WHERE lat IS NOT NULL AND lon IS NOT NULL;

CREATE TABLE people (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id  uuid NOT NULL REFERENCES households(id) ON DELETE CASCADE,
  name          text NOT NULL,
  age           smallint,
  party         text CHECK (party IN ('DEM','REP','BLK','WOR','CON','IND','OTH')),
  tier_letter   char(1) CHECK (tier_letter IN ('X','F','L','I')),
  tier_count    smallint NOT NULL DEFAULT 0,
  elections     jsonb,
  city          text NOT NULL,
  zip           text NOT NULL,
  turnout_prob  numeric(5,4),
  dem_lean_prob numeric(5,4),
  rep_lean_prob numeric(5,4),
  -- "NAME|CITY|ZIP5" — matches fec/nyboe cache lookup key
  donor_key     text GENERATED ALWAYS AS (upper(name) || '|' || upper(city) || '|' || zip) STORED,
  created_at    timestamptz DEFAULT now()
);

CREATE INDEX idx_people_household  ON people(household_id);
CREATE INDEX idx_people_party      ON people(party);
CREATE INDEX idx_people_tier       ON people(tier_letter, tier_count);
CREATE INDEX idx_people_donor_key  ON people(donor_key);
CREATE INDEX idx_people_turnout    ON people(turnout_prob DESC NULLS LAST);

CREATE TABLE donations (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  donor_key     text NOT NULL,
  source        text NOT NULL CHECK (source IN ('fec','nyboe')),
  donation_date date,
  amount        numeric(10,2),
  committee     text,
  confirmed     boolean NOT NULL DEFAULT true,
  created_at    timestamptz DEFAULT now()
);

CREATE INDEX idx_donations_donor_key  ON donations(donor_key);
CREATE INDEX idx_donations_committee  ON donations(lower(committee));

CREATE TABLE ev_scores (
  zip        text PRIMARY KEY,
  score      smallint NOT NULL,
  count      int NOT NULL DEFAULT 0,
  updated_at timestamptz DEFAULT now()
);

CREATE TABLE profiles (
  id         uuid PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  role       text NOT NULL DEFAULT 'canvasser' CHECK (role IN ('admin','canvasser')),
  name       text,
  created_at timestamptz DEFAULT now()
);

-- door_knocks added in migration 003 (Phase 4)

-- Auto-create profile row when a new auth user is created
CREATE OR REPLACE FUNCTION handle_new_user()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = public
AS $$
BEGIN
  INSERT INTO public.profiles (id)
  VALUES (NEW.id)
  ON CONFLICT DO NOTHING;
  RETURN NEW;
END;
$$;

CREATE TRIGGER on_auth_user_created
  AFTER INSERT ON auth.users
  FOR EACH ROW EXECUTE PROCEDURE handle_new_user();
