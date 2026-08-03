import { Pool } from "pg";

// Module-level pool — reused across requests in the same Node.js process.
//
// DATABASE_POOL_URL points at Supabase's transaction-mode pooler (port 6543)
// rather than DATABASE_URL's session-mode pooler (port 5432) — the mode
// Supabase recommends for serverless, since it doesn't hold a dedicated
// backend connection per client for the connection's lifetime. DATABASE_URL
// stays separate because model/db.py depends on its session-mode semantics
// (CREATE TEMP TABLE ... ON COMMIT DROP inside transactions) for score writes.
const pool = new Pool({
  connectionString: process.env.DATABASE_POOL_URL || process.env.DATABASE_URL,
  max: 20,
  idleTimeoutMillis: 30000,
  connectionTimeoutMillis: 5000,
});

export default pool;
