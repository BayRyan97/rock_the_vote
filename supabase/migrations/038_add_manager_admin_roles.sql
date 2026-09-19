-- Two new roles, each a "full access" variant of an existing scoped role:
--   running_admin          -- like running, but sees ALL notes, not just
--                              notes canvassed for them. For a candidate who
--                              has agreed to share canvassing data.
--   campaign_manager_admin -- like campaign_manager, but sees ALL notes
--                              instead of being scoped to one campaign_name.
-- See 039 (profiles.campaign_name) and 040/041 for how these are used.
ALTER TABLE profiles
  DROP CONSTRAINT IF EXISTS profiles_role_check;

ALTER TABLE profiles
  ADD CONSTRAINT profiles_role_check
  CHECK (role IN (
    'admin', 'canvasser', 'dfli', 'running', 'campaign_manager',
    'running_admin', 'campaign_manager_admin'
  ));
