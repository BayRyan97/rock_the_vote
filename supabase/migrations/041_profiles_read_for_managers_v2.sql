-- Supersedes 037's version of this policy. Two additions:
--
-- 1. running_admin/campaign_manager_admin join the roles that can read every
--    profile (needed for author-name resolution in /api/notes, same reason
--    037 added admin/running/campaign_manager).
--
-- 2. ANY authenticated user (including plain canvassers) can now read
--    profiles rows where role is running or running_admin -- these are the
--    candidates canvassers pick from in the new "who are you canvassing
--    for" suggestions on the note form. Campaign managers are not exposed
--    this way since they aren't candidates and don't appear in that list.
DROP POLICY IF EXISTS "users_read_own_profile" ON profiles;

CREATE POLICY "users_read_own_profile"
  ON profiles FOR SELECT
  USING (
    id = (select auth.uid())
    OR (select get_user_role()) IN (
      'admin', 'running', 'campaign_manager', 'running_admin', 'campaign_manager_admin'
    )
    OR role IN ('running', 'running_admin')
  );
