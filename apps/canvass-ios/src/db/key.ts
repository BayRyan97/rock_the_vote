/**
 * Database key handling for the encrypted local store (task P1-04, spec §9.2
 * and §11: "Offline cache on a crashed device -> Encrypted SQLite").
 *
 * The key never leaves the device and is never derived from anything the
 * canvasser types. A volunteer-owned phone has no MDM (§14a), so the Keychain
 * is the only hardware-backed protection available to us.
 *
 * Both exported functions take their dependencies as arguments rather than
 * importing expo-secure-store and expo-crypto directly, so the logic here is
 * unit-testable off-device. `src/db/open.ts` wires in the real modules.
 */

/** Keychain entry name. Versioned so a v2 key format can coexist during a migration. */
export const DB_KEY_STORE_KEY = 'bellwether.db.key.v1';

/** 256-bit, matching SQLCipher's default cipher (AES-256). */
export const DB_KEY_BYTES = 32;

/** The slice of expo-secure-store this module needs. */
export type SecureStoreLike = {
  getItemAsync(key: string, options?: object): Promise<string | null>;
  setItemAsync(key: string, value: string, options?: object): Promise<void>;
  deleteItemAsync(key: string, options?: object): Promise<void>;
};

/** `expo-crypto`'s getRandomBytes. Must be a CSPRNG -- Math.random is not one. */
export type RandomBytes = (byteCount: number) => Uint8Array;

/**
 * Format a key for `PRAGMA key`.
 *
 * SQLCipher accepts either a passphrase, which it stretches with PBKDF2, or a
 * raw key in `x'<hex>'` form, which it uses directly. We generate 32
 * full-entropy bytes, so PBKDF2 would add no security -- only a per-open cost
 * (256k SHA-512 iterations by default). Raw it is.
 *
 * Exported so a test can pin the format: a passphrase-shaped string here would
 * still "work", silently, while changing how the key is treated.
 */
export function toRawKeyPragmaValue(bytes: Uint8Array): string {
  if (bytes.length !== DB_KEY_BYTES) {
    throw new Error(`Database key must be ${DB_KEY_BYTES} bytes, got ${bytes.length}.`);
  }

  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
  return `x'${hex}'`;
}

/**
 * Return the device's database key, creating one on first run.
 *
 * `WHEN_UNLOCKED_THIS_DEVICE_ONLY` (passed in by `open.ts`) is deliberate and
 * load-bearing:
 * - `WHEN_UNLOCKED` keeps the key out of reach while the phone is locked,
 *   which is the lost-phone case in §11;
 * - `THIS_DEVICE_ONLY` keeps it out of iCloud Keychain backups, so a restore
 *   onto a second phone cannot reopen a cache full of voter names.
 */
export async function getOrCreateDatabaseKey(
  store: SecureStoreLike,
  randomBytes: RandomBytes,
  keychainOptions: object,
): Promise<string> {
  const existing = await store.getItemAsync(DB_KEY_STORE_KEY, keychainOptions);
  if (existing) return existing;

  const created = toRawKeyPragmaValue(randomBytes(DB_KEY_BYTES));
  await store.setItemAsync(DB_KEY_STORE_KEY, created, keychainOptions);
  return created;
}

/**
 * Forget the key, which makes the database file permanently unreadable.
 *
 * P1-05 calls this as the last step of a wipe. Deleting the key is what makes
 * a wipe irreversible even if the file itself survives -- an unlinked SQLite
 * file can linger in free space, but without the key it is ciphertext.
 */
export async function forgetDatabaseKey(
  store: SecureStoreLike,
  keychainOptions: object,
): Promise<void> {
  await store.deleteItemAsync(DB_KEY_STORE_KEY, keychainOptions);
}
