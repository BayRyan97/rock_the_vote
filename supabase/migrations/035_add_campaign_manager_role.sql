-- Add the campaign_manager role: a non-admin managerial role that can browse
-- all canvass notes across turfs/canvassers (see 036/037 and the new
-- /api/notes manager view) without admin's write/config powers.
ALTER TABLE profiles
  DROP CONSTRAINT IF EXISTS profiles_role_check;

ALTER TABLE profiles
  ADD CONSTRAINT profiles_role_check
  CHECK (role IN ('admin', 'canvasser', 'dfli', 'running', 'campaign_manager'));
