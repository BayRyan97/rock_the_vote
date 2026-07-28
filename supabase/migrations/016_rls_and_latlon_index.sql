-- Fix 1: RLS policy overhead
-- auth.role() and get_user_role() were evaluated once per row.
-- Wrapping in (select ...) forces a single evaluation per query.

DROP POLICY IF EXISTS "authenticated_read_households" ON households;
DROP POLICY IF EXISTS "admins_write_households"       ON households;
DROP POLICY IF EXISTS "authenticated_read_people"     ON people;
DROP POLICY IF EXISTS "admins_write_people"           ON people;
DROP POLICY IF EXISTS "authenticated_read_donations"  ON donations;
DROP POLICY IF EXISTS "admins_write_donations"        ON donations;
DROP POLICY IF EXISTS "authenticated_read_ev_scores"  ON ev_scores;
DROP POLICY IF EXISTS "admins_write_ev_scores"        ON ev_scores;
DROP POLICY IF EXISTS "users_read_own_profile"        ON profiles;
DROP POLICY IF EXISTS "users_update_own_name"         ON profiles;
DROP POLICY IF EXISTS "admins_manage_profiles"        ON profiles;

-- households
CREATE POLICY "authenticated_read_households"
  ON households FOR SELECT
  USING ((select auth.role()) = 'authenticated');

CREATE POLICY "admins_write_households"
  ON households FOR ALL
  USING ((select get_user_role()) = 'admin')
  WITH CHECK ((select get_user_role()) = 'admin');

-- people
CREATE POLICY "authenticated_read_people"
  ON people FOR SELECT
  USING ((select auth.role()) = 'authenticated');

CREATE POLICY "admins_write_people"
  ON people FOR ALL
  USING ((select get_user_role()) = 'admin')
  WITH CHECK ((select get_user_role()) = 'admin');

-- donations
CREATE POLICY "authenticated_read_donations"
  ON donations FOR SELECT
  USING ((select auth.role()) = 'authenticated');

CREATE POLICY "admins_write_donations"
  ON donations FOR ALL
  USING ((select get_user_role()) = 'admin')
  WITH CHECK ((select get_user_role()) = 'admin');

-- ev_scores
CREATE POLICY "authenticated_read_ev_scores"
  ON ev_scores FOR SELECT
  USING ((select auth.role()) = 'authenticated');

CREATE POLICY "admins_write_ev_scores"
  ON ev_scores FOR ALL
  USING ((select get_user_role()) = 'admin')
  WITH CHECK ((select get_user_role()) = 'admin');

-- profiles
CREATE POLICY "users_read_own_profile"
  ON profiles FOR SELECT
  USING (id = (select auth.uid()) OR (select get_user_role()) = 'admin');

CREATE POLICY "users_update_own_name"
  ON profiles FOR UPDATE
  USING (id = (select auth.uid()))
  WITH CHECK (id = (select auth.uid()) AND role = (SELECT role FROM profiles WHERE id = (select auth.uid())));

CREATE POLICY "admins_manage_profiles"
  ON profiles FOR ALL
  USING ((select get_user_role()) = 'admin')
  WITH CHECK ((select get_user_role()) = 'admin');


-- Fix 2: lat/lon bounding box index for map viewport queries
CREATE INDEX IF NOT EXISTS idx_households_lat_lon
  ON households (lat, lon)
  WHERE lat IS NOT NULL AND lon IS NOT NULL;

-- Fix 3: score_total index on households for ORDER BY + partial filter
CREATE INDEX IF NOT EXISTS idx_households_score_total
  ON households (score_total DESC)
  WHERE score_total > 0;
