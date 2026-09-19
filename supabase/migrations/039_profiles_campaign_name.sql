-- Which candidate's campaign a campaign_manager is assigned to -- set by an
-- admin in User Management (ProfilesTable), matched against
-- canvass_notes.canvassing_for (040) to scope that manager's notes view to
-- just their candidate. Free text, not a FK, matching canvassing_for's own
-- design: it identifies a candidate by the same name string a canvasser
-- picks from the "who are you canvassing for" suggestions, not a profile id.
ALTER TABLE profiles ADD COLUMN campaign_name text;
