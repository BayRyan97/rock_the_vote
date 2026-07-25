-- Row Level Security policies
-- Canvassers: read all voter data, write only their own door_knocks
-- Admins: full access

ALTER TABLE households    ENABLE ROW LEVEL SECURITY;
ALTER TABLE people        ENABLE ROW LEVEL SECURITY;
ALTER TABLE donations     ENABLE ROW LEVEL SECURITY;
ALTER TABLE ev_scores     ENABLE ROW LEVEL SECURITY;
ALTER TABLE profiles      ENABLE ROW LEVEL SECURITY;

-- Helper: get current user's role without recursion
CREATE OR REPLACE FUNCTION get_user_role()
RETURNS text LANGUAGE sql SECURITY DEFINER STABLE
AS $$ SELECT role FROM public.profiles WHERE id = auth.uid() $$;

-- households
CREATE POLICY "authenticated_read_households"
  ON households FOR SELECT
  USING (auth.role() = 'authenticated');

CREATE POLICY "admins_write_households"
  ON households FOR ALL
  USING (get_user_role() = 'admin')
  WITH CHECK (get_user_role() = 'admin');

-- people
CREATE POLICY "authenticated_read_people"
  ON people FOR SELECT
  USING (auth.role() = 'authenticated');

CREATE POLICY "admins_write_people"
  ON people FOR ALL
  USING (get_user_role() = 'admin')
  WITH CHECK (get_user_role() = 'admin');

-- donations
CREATE POLICY "authenticated_read_donations"
  ON donations FOR SELECT
  USING (auth.role() = 'authenticated');

CREATE POLICY "admins_write_donations"
  ON donations FOR ALL
  USING (get_user_role() = 'admin')
  WITH CHECK (get_user_role() = 'admin');

-- ev_scores (read-only for everyone)
CREATE POLICY "authenticated_read_ev_scores"
  ON ev_scores FOR SELECT
  USING (auth.role() = 'authenticated');

CREATE POLICY "admins_write_ev_scores"
  ON ev_scores FOR ALL
  USING (get_user_role() = 'admin')
  WITH CHECK (get_user_role() = 'admin');

-- profiles
CREATE POLICY "users_read_own_profile"
  ON profiles FOR SELECT
  USING (id = auth.uid() OR get_user_role() = 'admin');

CREATE POLICY "users_update_own_name"
  ON profiles FOR UPDATE
  USING (id = auth.uid())
  WITH CHECK (id = auth.uid() AND role = (SELECT role FROM profiles WHERE id = auth.uid()));

CREATE POLICY "admins_manage_profiles"
  ON profiles FOR ALL
  USING (get_user_role() = 'admin')
  WITH CHECK (get_user_role() = 'admin');
