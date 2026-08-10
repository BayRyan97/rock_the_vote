-- Suffolk's 2026-08 voter-phone export tags each number mobile/landline;
-- Nassau's boe_contacts rows predate this and leave it null.
ALTER TABLE boe_contacts ADD COLUMN phone_type text;
