import { DB_KEY_STORE_KEY, type SecureStoreLike } from './key';
import {
  DatabaseKeyMismatchError,
  openEncryptedDatabase,
  SQLCipherUnavailableError,
  type DatabaseLike,
  type OpenDeps,
} from './open';
import { SCHEMA_VERSION } from './schema';

type FakeOptions = {
  /** What `PRAGMA cipher_version` reports. Empty simulates plain SQLite. */
  cipherVersion?: string;
  /** Simulate a file that the key does not decrypt. */
  failMasterRead?: boolean;
  startingUserVersion?: number;
};

/** Records every statement, which is what these tests are really about. */
function fakeDb(options: FakeOptions = {}) {
  const { cipherVersion = '4.6.1', failMasterRead = false, startingUserVersion = 0 } = options;
  const statements: string[] = [];
  let closed = false;

  const db: DatabaseLike = {
    async execAsync(sql) {
      statements.push(sql.trim());
    },
    async getFirstAsync<T>(sql: string): Promise<T | null> {
      statements.push(sql.trim());

      if (sql.includes('cipher_version')) {
        return (cipherVersion ? { cipher_version: cipherVersion } : {}) as T;
      }
      if (sql.includes('sqlite_master')) {
        if (failMasterRead) throw new Error('file is not a database');
        return { n: 0 } as T;
      }
      if (sql.includes('user_version')) {
        return { user_version: startingUserVersion } as T;
      }
      return null;
    },
    async closeAsync() {
      closed = true;
    },
  };

  return { db, statements, wasClosed: () => closed };
}

function deps(db: DatabaseLike, store?: SecureStoreLike): OpenDeps {
  const items = new Map<string, string>();
  return {
    openDatabase: async () => db,
    store: store ?? {
      async getItemAsync(k) {
        return items.get(k) ?? null;
      },
      async setItemAsync(k, v) {
        items.set(k, v);
      },
      async deleteItemAsync(k) {
        items.delete(k);
      },
    },
    randomBytes: (n) => Uint8Array.from({ length: n }, (_, i) => i),
    keychainOptions: { keychainAccessible: 'WHEN_UNLOCKED_THIS_DEVICE_ONLY' },
  };
}

describe('openEncryptedDatabase', () => {
  it('issues PRAGMA key as the very first statement', async () => {
    const { db, statements } = fakeDb();

    await openEncryptedDatabase(deps(db));

    // Anything before the key either fails or, on a new file, creates an
    // unencrypted database. This ordering is the security property.
    expect(statements[0]).toMatch(/^PRAGMA key = "x'[0-9a-f]{64}'"$/);
  });

  it('verifies the key before touching the schema', async () => {
    const { db, statements } = fakeDb();

    await openEncryptedDatabase(deps(db));

    const cipherAt = statements.findIndex((s) => s.includes('cipher_version'));
    const masterAt = statements.findIndex((s) => s.includes('sqlite_master'));
    const migrationAt = statements.findIndex((s) => s.includes('CREATE TABLE'));

    expect(cipherAt).toBeGreaterThan(-1);
    expect(masterAt).toBeGreaterThan(cipherAt);
    expect(migrationAt).toBeGreaterThan(masterAt);
  });

  it('refuses to run against plain SQLite', async () => {
    // The failure this class exists for: `PRAGMA key` against plain SQLite is
    // accepted and ignored, so every read and write succeeds while the cache
    // sits on disk in the clear. Nothing else in the JS layer would notice.
    const { db, statements, wasClosed } = fakeDb({ cipherVersion: '' });

    await expect(openEncryptedDatabase(deps(db))).rejects.toThrow(SQLCipherUnavailableError);

    expect(statements.some((s) => s.includes('CREATE TABLE'))).toBe(false);
    expect(wasClosed()).toBe(true);
  });

  it('names the rebuild in the SQLCipher error, since a reload cannot fix it', async () => {
    const { db } = fakeDb({ cipherVersion: '' });

    await expect(openEncryptedDatabase(deps(db))).rejects.toThrow(/useSQLCipher/);
    await expect(openEncryptedDatabase(deps(db))).rejects.toThrow(/development client/);
  });

  it('reports a key mismatch rather than a raw SQLite error', async () => {
    const { db, wasClosed } = fakeDb({ failMasterRead: true });

    await expect(openEncryptedDatabase(deps(db))).rejects.toThrow(DatabaseKeyMismatchError);
    expect(wasClosed()).toBe(true);
  });

  it('turns foreign keys on, so the schema CASCADEs actually fire', async () => {
    const { db, statements } = fakeDb();

    await openEncryptedDatabase(deps(db));

    expect(statements).toContain('PRAGMA foreign_keys = ON');
  });

  it('creates the key on first open and reuses it on the next', async () => {
    const items = new Map<string, string>();
    const store: SecureStoreLike = {
      async getItemAsync(k) {
        return items.get(k) ?? null;
      },
      async setItemAsync(k, v) {
        items.set(k, v);
      },
      async deleteItemAsync(k) {
        items.delete(k);
      },
    };

    const first = fakeDb();
    await openEncryptedDatabase(deps(first.db, store));
    const created = items.get(DB_KEY_STORE_KEY);

    const second = fakeDb({ startingUserVersion: SCHEMA_VERSION });
    await openEncryptedDatabase(deps(second.db, store));

    expect(created).toBeDefined();
    expect(second.statements[0]).toBe(`PRAGMA key = "${created}"`);
  });

  it('skips migrations on an already-current database', async () => {
    const { db, statements } = fakeDb({ startingUserVersion: SCHEMA_VERSION });

    await openEncryptedDatabase(deps(db));

    expect(statements.some((s) => s.includes('CREATE TABLE'))).toBe(false);
  });

  it('refuses to open a database written by a newer build', async () => {
    // Running the older schema against a newer file would drop columns the
    // newer build wrote -- including queued outbox events.
    const { db } = fakeDb({ startingUserVersion: SCHEMA_VERSION + 1 });

    await expect(openEncryptedDatabase(deps(db))).rejects.toThrow(/newer than this build/);
  });

  it('wraps each migration in a transaction', async () => {
    const { db, statements } = fakeDb();

    await openEncryptedDatabase(deps(db));

    const migration = statements.find((s) => s.includes('CREATE TABLE'));
    expect(migration).toMatch(/^BEGIN;/);
    expect(migration).toMatch(/COMMIT;$/);
    expect(migration).toContain(`PRAGMA user_version = ${SCHEMA_VERSION}`);
  });
});
