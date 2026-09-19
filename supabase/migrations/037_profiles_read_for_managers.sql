-- Let running/campaign_manager read the full profiles table (name lookup),
-- matching admin's existing exemption. Needed so the new manager notes view
-- (/api/notes) and its canvasser-filter dropdown can see every author's
-- name, not just the viewer's own -- otherwise PostgREST's embedded
-- `profiles(name)` join silently nulls out rows RLS denies. This also fixes
-- the same pre-existing gap for "running" users viewing per-household note
-- history via CanvassNoteModal (in scope only as a side effect; that route
-- itself is unchanged). Write policies on profiles are untouched, so
-- running/campaign_manager still cannot edit anyone's role or name.
DROP POLICY IF EXISTS "users_read_own_profile" ON profiles;

CREATE POLICY "users_read_own_profile"
  ON profiles FOR SELECT
  USING (
    id = (select auth.uid())
    OR (select get_user_role()) IN ('admin', 'running', 'campaign_manager')
  );
