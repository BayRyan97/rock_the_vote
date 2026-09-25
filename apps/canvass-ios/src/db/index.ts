/**
 * The app's entry point to the encrypted local store (task P1-04).
 *
 * This is the only file that imports the native modules; everything under
 * `src/db/` is pure and injected, so the logic is testable off-device.
 */

import * as Crypto from 'expo-crypto';
import { File } from 'expo-file-system';
import * as SecureStore from 'expo-secure-store';
import * as SQLite from 'expo-sqlite';

import { forgetDatabaseKey } from './key';
import { openEncryptedDatabase, type DatabaseLike } from './open';
import { HEADER_BYTES, verifyDatabaseEncryptedOnDisk, type VerificationResult } from './verify';

export { DATABASE_NAME, DatabaseKeyMismatchError, SQLCipherUnavailableError } from './open';
export { CACHE_TABLES, SCHEMA_VERSION } from './schema';
export { formatHeader } from './verify';
export type { DatabaseLike } from './open';
export type { VerificationResult } from './verify';

/**
 * See `key.ts` for why both halves matter: WHEN_UNLOCKED for the lost-phone
 * case, THIS_DEVICE_ONLY to keep the key out of iCloud Keychain backups.
 */
const KEYCHAIN_OPTIONS: SecureStore.SecureStoreOptions = {
  keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY,
};

/**
 * One connection per app run. SQLCipher derives its page cipher once per
 * connection, so reopening per query would be both slower and more places for
 * the key to be handled.
 */
let connection: Promise<DatabaseLike> | null = null;

export function getDatabase(): Promise<DatabaseLike> {
  connection ??= openEncryptedDatabase({
    openDatabase: (name) => SQLite.openDatabaseAsync(name),
    store: SecureStore,
    randomBytes: (byteCount) => Crypto.getRandomBytes(byteCount),
    keychainOptions: KEYCHAIN_OPTIONS,
  });

  return connection;
}

/**
 * Run P1-04's acceptance check against the real file on disk.
 *
 * Writes nothing and reads only the first 16 bytes -- never the voter data in
 * the rest of the file. See `verify.ts` for why the header is sufficient proof.
 */
export async function verifyEncryptionOnDevice(): Promise<VerificationResult> {
  const db = (await getDatabase()) as SQLite.SQLiteDatabase;

  // Re-read rather than trusting the open-time check: this must observe the
  // state of the running app, not a value cached from startup.
  const cipher = await db.getFirstAsync<{ cipher_version?: string }>('PRAGMA cipher_version');

  return verifyDatabaseEncryptedOnDisk({
    databasePath: db.databasePath,
    cipherVersion: cipher?.cipher_version ?? null,
    readHeader: async (path, byteCount) => {
      const bytes = await new File(path).bytes();
      return bytes.slice(0, byteCount);
    },
  });
}

export { HEADER_BYTES };

/**
 * Close the connection and forget the key.
 *
 * P1-05 owns the wipe triggers (End Shift, 12-hour idle, logout, remote-wipe
 * flag) and the deletion of the file itself. This is the part that makes any
 * of them irreversible, and it lives here because it is the inverse of
 * `getDatabase`.
 */
export async function closeAndForgetDatabase(): Promise<void> {
  const open = connection;
  connection = null;

  if (open) {
    // A failed close must not skip the key deletion below: a surviving key is
    // the one outcome a wipe cannot tolerate.
    await open.then((db) => db.closeAsync()).catch(() => undefined);
  }

  await forgetDatabaseKey(SecureStore, KEYCHAIN_OPTIONS);
}
