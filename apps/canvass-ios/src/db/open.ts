/**
 * Opening the encrypted local store (task P1-04, spec §9.2).
 *
 * The order of statements here is the whole security property, so it is
 * expressed as one function with injected dependencies and pinned by
 * `open.test.ts` rather than left to be re-derived at each call site.
 */

import { getOrCreateDatabaseKey, type RandomBytes, type SecureStoreLike } from './key';
import { MIGRATIONS, SCHEMA_VERSION } from './schema';

/** On-disk name. SQLCipher encrypts the whole file, header included. */
export const DATABASE_NAME = 'bellwether-canvass.db';

/** The slice of an expo-sqlite database this module uses. */
export type DatabaseLike = {
  execAsync(sql: string): Promise<void>;
  getFirstAsync<T>(sql: string): Promise<T | null>;
  closeAsync(): Promise<void>;
};

export type OpenDatabaseFn = (name: string) => Promise<DatabaseLike>;

export type OpenDeps = {
  openDatabase: OpenDatabaseFn;
  store: SecureStoreLike;
  randomBytes: RandomBytes;
  /** Passed straight to expo-secure-store; see `key.ts` for why it matters. */
  keychainOptions: object;
};

/**
 * Thrown when the native build has plain SQLite rather than SQLCipher.
 *
 * This is the failure worth naming. `PRAGMA key` against plain SQLite is
 * *accepted and ignored* -- every read and write then succeeds, and the cache
 * sits on disk in the clear with nothing in the JS layer to notice. The only
 * signal is `PRAGMA cipher_version`, which returns nothing on plain SQLite.
 */
export class SQLCipherUnavailableError extends Error {
  constructor() {
    super(
      'Database opened without SQLCipher: `PRAGMA cipher_version` returned nothing. ' +
        'The native build is missing the expo-sqlite `useSQLCipher` plugin option. ' +
        'Rebuild the development client -- a JS reload cannot fix this.',
    );
    this.name = 'SQLCipherUnavailableError';
  }
}

/** Thrown when the file exists but the key does not open it. */
export class DatabaseKeyMismatchError extends Error {
  constructor(cause: unknown) {
    super(
      'Database exists but could not be read with the stored key. The cache must be ' +
        'discarded and refetched; it cannot be recovered.',
    );
    this.name = 'DatabaseKeyMismatchError';
    this.cause = cause;
  }
}

/**
 * Open the cache, keyed, verified and migrated.
 *
 * Statement order, all of it load-bearing:
 * 1. `PRAGMA key` must be the first statement on the connection. SQLCipher
 *    reads the header when the first real query runs; a query before the key
 *    fails, and on a *new* file it would create an unencrypted database.
 * 2. `PRAGMA cipher_version` proves the binary is SQLCipher (see above).
 * 3. A read against `sqlite_master` proves the key is the right one -- the
 *    pragma itself never fails, it just leaves the pages undecipherable.
 * 4. Foreign keys on, so the CASCADE rules in the schema actually fire.
 * 5. Migrations last, once the connection is known good.
 */
export async function openEncryptedDatabase(deps: OpenDeps): Promise<DatabaseLike> {
  const key = await getOrCreateDatabaseKey(deps.store, deps.randomBytes, deps.keychainOptions);
  const db = await deps.openDatabase(DATABASE_NAME);

  // 1. Key first, always.
  await db.execAsync(`PRAGMA key = "${key}"`);

  // 2. Are we actually on SQLCipher?
  const cipher = await db.getFirstAsync<{ cipher_version?: string }>('PRAGMA cipher_version');
  if (!cipher?.cipher_version) {
    await db.closeAsync();
    throw new SQLCipherUnavailableError();
  }

  // 3. Is it the right key?
  try {
    await db.getFirstAsync('SELECT count(*) AS n FROM sqlite_master');
  } catch (error) {
    await db.closeAsync();
    throw new DatabaseKeyMismatchError(error);
  }

  // 4. CASCADE deletes are off by default in SQLite.
  await db.execAsync('PRAGMA foreign_keys = ON');

  // 5. Schema.
  await migrate(db);

  return db;
}

/**
 * Apply pending migrations.
 *
 * `user_version` is stored inside the (encrypted) database, so this reveals
 * nothing to an attacker holding the file.
 */
export async function migrate(db: DatabaseLike): Promise<void> {
  const row = await db.getFirstAsync<{ user_version: number }>('PRAGMA user_version');
  const current = row?.user_version ?? 0;

  if (current > SCHEMA_VERSION) {
    throw new Error(
      `Database is at schema version ${current}, newer than this build's ${SCHEMA_VERSION}. ` +
        'Downgrading would drop data written by a later version.',
    );
  }

  for (let version = current; version < SCHEMA_VERSION; version += 1) {
    // Each migration is one transaction: a half-applied schema on a phone
    // that lost power mid-migration has no path back.
    await db.execAsync(`BEGIN; ${MIGRATIONS[version]} PRAGMA user_version = ${version + 1}; COMMIT;`);
  }
}
