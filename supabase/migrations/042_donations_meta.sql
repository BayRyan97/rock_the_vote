-- donations_meta: put the dashboard's headline table under version control.
--
-- This table has NO DDL anywhere in this repository. `grep -rn donations_meta
-- supabase/migrations/` returns nothing across all 41 prior migrations, yet two
-- things in the tree depend on it every week:
--
--   build/migrate_donations_psycopg2.py  refresh_aggregates() UPDATEs it after
--                                        every load (Sunday cron).
--   app/api/donations/stats/route.ts:35  SELECTs the four counters from it to
--                                        render the /donations headline numbers.
--
-- It was created by hand against the live database and never written down, so a
-- database rebuilt from these migrations gets an app that queries a table that
-- does not exist and a loader whose UPDATE errors. This migration is catching up
-- to reality rather than changing it.
--
-- TWO CORRECTIONS over the version that sat unmerged on the abandoned
-- model-donation-v1 branch (archived at tag archive/model-donation-v1):
--
--   1. The timestamp column is computed_at, NOT refreshed_at. The branch
--      declared refreshed_at, which disagrees with the only writer in the tree
--      -- migrate_donations_psycopg2.py sets `computed_at = now()`. Applying the
--      branch version to a fresh database produces a loader that fails on every
--      run with "column computed_at does not exist". The live writer wins.
--
--   2. The id = 1 row is SEEDED here. The loader only ever runs
--      `UPDATE ... WHERE id = 1`; against an empty table that is a zero-row
--      no-op, forever, with no error. stats/route.ts then renders zeros rather
--      than failing, so the dashboard looks merely uninteresting instead of
--      broken. Seeding the row is what makes the weekly refresh take effect.
--
-- Written to converge whichever state the target is in: the live database
-- already has some version of this table (shape unverified -- run \d
-- donations_meta and reconcile if it differs), a fresh one has none, and this
-- produces the same result either way.

CREATE TABLE IF NOT EXISTS donations_meta (
  id               smallint PRIMARY KEY,
  confirmed_count  bigint  NOT NULL DEFAULT 0,
  possible_count   bigint  NOT NULL DEFAULT 0,
  confirmed_total  numeric NOT NULL DEFAULT 0,
  confirmed_donors bigint  NOT NULL DEFAULT 0,
  computed_at      timestamptz
);

-- Converge an existing hand-made instance whose columns may predate any of the
-- above. Each is a no-op where the column is already present.
ALTER TABLE donations_meta ADD COLUMN IF NOT EXISTS confirmed_count  bigint  NOT NULL DEFAULT 0;
ALTER TABLE donations_meta ADD COLUMN IF NOT EXISTS possible_count   bigint  NOT NULL DEFAULT 0;
ALTER TABLE donations_meta ADD COLUMN IF NOT EXISTS confirmed_total  numeric NOT NULL DEFAULT 0;
ALTER TABLE donations_meta ADD COLUMN IF NOT EXISTS confirmed_donors bigint  NOT NULL DEFAULT 0;
ALTER TABLE donations_meta ADD COLUMN IF NOT EXISTS computed_at      timestamptz;

-- Singleton by construction: stats/route.ts hardcodes `WHERE id = 1`, so a
-- second row would be silently invisible rather than an error.
INSERT INTO donations_meta (id) VALUES (1) ON CONFLICT (id) DO NOTHING;

COMMENT ON TABLE donations_meta IS
  'Single-row (id = 1) precomputed counters for the /donations dashboard. '
  'Written by build/migrate_donations_psycopg2.py refresh_aggregates(); '
  'read by app/api/donations/stats/route.ts. Not a cache that can be rebuilt '
  'on demand -- if it is stale, the dashboard is stale.';
COMMENT ON COLUMN donations_meta.computed_at IS
  'When refresh_aggregates() last ran. NULL means the weekly loader has never '
  'successfully refreshed this row.';

-- Same read/write split as households/people/donations (001, tightened in 016)
-- and turfs (017): any authenticated user reads, only admins write. The Next.js
-- app reaches this table through lib/db.ts (a direct pg pool as the owner role)
-- which bypasses RLS entirely -- these policies govern the PostgREST path.
--
-- donation_summaries is deliberately NOT touched here. It has had no RLS since
-- 009, and enabling it now would change behaviour for any existing PostgREST
-- reader; that is a separate decision from making this table reproducible.
ALTER TABLE donations_meta ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "authenticated_read_donations_meta" ON donations_meta;
CREATE POLICY "authenticated_read_donations_meta"
  ON donations_meta FOR SELECT
  USING ((select auth.role()) = 'authenticated');

DROP POLICY IF EXISTS "admins_write_donations_meta" ON donations_meta;
CREATE POLICY "admins_write_donations_meta"
  ON donations_meta FOR ALL
  USING ((select get_user_role()) = 'admin')
  WITH CHECK ((select get_user_role()) = 'admin');
