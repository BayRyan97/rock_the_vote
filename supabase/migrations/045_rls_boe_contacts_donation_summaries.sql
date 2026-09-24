-- boe_contacts and donation_summaries were the only two tables in the public
-- schema left without Row Level Security (Supabase's automated scanner flagged
-- boe_contacts as rls_disabled_in_public). Both are reachable directly through
-- PostgREST with the public anon key, bypassing the Next.js app entirely --
-- lib/db.ts's direct pg connection (which the app itself uses) is unaffected
-- either way, since that path already bypasses RLS as the owner role.
--
-- boe_contacts holds voter name, DOB, email, phone, and residential/mailing
-- address -- the most sensitive table in the database, and until this migration
-- it had never had RLS since its creation in 012_boe_contacts.sql.
--
-- donation_summaries (donor_key, total_donated, donation_count) was left
-- unprotected on purpose in 042_donations_meta.sql, as a separate decision from
-- that migration. Closing it now for the same reason boe_contacts is closed:
-- both are readable (and, with no policy at all, writable/deletable) by anyone
-- holding the anon key, which ships in every page load.
--
-- Same read/write split as every other table (001/016, 017, 020, 030, 042):
-- any authenticated user reads, only admins write.

ALTER TABLE boe_contacts ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "authenticated_read_boe_contacts" ON boe_contacts;
CREATE POLICY "authenticated_read_boe_contacts"
  ON boe_contacts FOR SELECT
  USING ((select auth.role()) = 'authenticated');

DROP POLICY IF EXISTS "admins_write_boe_contacts" ON boe_contacts;
CREATE POLICY "admins_write_boe_contacts"
  ON boe_contacts FOR ALL
  USING ((select get_user_role()) = 'admin')
  WITH CHECK ((select get_user_role()) = 'admin');

ALTER TABLE donation_summaries ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "authenticated_read_donation_summaries" ON donation_summaries;
CREATE POLICY "authenticated_read_donation_summaries"
  ON donation_summaries FOR SELECT
  USING ((select auth.role()) = 'authenticated');

DROP POLICY IF EXISTS "admins_write_donation_summaries" ON donation_summaries;
CREATE POLICY "admins_write_donation_summaries"
  ON donation_summaries FOR ALL
  USING ((select get_user_role()) = 'admin')
  WITH CHECK ((select get_user_role()) = 'admin');
