/**
 * The app's entry point to the encrypted local store (task P1-04).
 *
 * This is the only file that imports the native modules; everything under
 * `src/db/` is pure and injected, so the logic is testable off-device.
 */

import * as Crypto from 'expo-crypto';
import * as SecureStore from 'expo-secure-store';
import * as SQLite from 'expo-sqlite';

import { forgetDatabaseKey } from './key';
import { openEncryptedDatabase, type DatabaseLike } from './open';

export { DATABASE_NAME, DatabaseKeyMismatchError, SQLCipherUnavailableError } from './open';
export { CACHE_TABLES, SCHEMA_VERSION } from './schema';
export type { DatabaseLike } from './open';

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
