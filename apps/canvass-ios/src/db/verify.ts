/**
 * On-device proof that the cache is encrypted at rest (task P1-04's acceptance
 * check: "test that the DB file is unreadable without the key").
 *
 * Why this exists as app code rather than a Jest test: `jest-expo` runs in Node
 * with no native SQLCipher, so nothing in the test suite can observe the actual
 * bytes on disk. The suite pins the *contract* that produces encryption --
 * PRAGMA ordering, the cipher_version guard -- but only a real device can show
 * the ciphertext.
 *
 * How it proves it without a Mac, Xcode, or extracting the file: every
 * unencrypted SQLite database begins with the exact 16-byte string
 * "SQLite format 3\0" (SQLite file-format spec, header offset 0). SQLCipher
 * encrypts the header along with everything else and puts a random salt in
 * those bytes instead. So if byte 0 of the file is not that magic string, the
 * file is not a readable SQLite database -- which is the claim under test.
 */

/** The SQLite file header, as bytes. Present iff the database is unencrypted. */
export const SQLITE_PLAINTEXT_MAGIC = new Uint8Array([
  0x53, 0x51, 0x4c, 0x69, 0x74, 0x65, 0x20, 0x66, 0x6f, 0x72, 0x6d, 0x61, 0x74, 0x20, 0x33, 0x00,
]);

export const HEADER_BYTES = SQLITE_PLAINTEXT_MAGIC.length;

/**
 * True when the first bytes of a file are SQLite's plaintext magic string.
 *
 * Pure, so the comparison itself is covered in CI even though the file read
 * around it is not.
 */
export function looksLikePlaintextSqlite(header: Uint8Array): boolean {
  if (header.length < HEADER_BYTES) return false;

  for (let i = 0; i < HEADER_BYTES; i += 1) {
    if (header[i] !== SQLITE_PLAINTEXT_MAGIC[i]) return false;
  }

  return true;
}

/** Render bytes for display on the device, so a failure is legible on-screen. */
export function formatHeader(header: Uint8Array): string {
  const hex = Array.from(header.slice(0, HEADER_BYTES), (b) => b.toString(16).padStart(2, '0')).join(
    ' ',
  );
  const ascii = Array.from(header.slice(0, HEADER_BYTES), (b) =>
    b >= 0x20 && b <= 0x7e ? String.fromCharCode(b) : '.',
  ).join('');

  return `${hex}\n"${ascii}"`;
}

export type VerificationResult = {
  passed: boolean;
  /** SQLCipher's reported version, or null when the build is plain SQLite. */
  cipherVersion: string | null;
  databasePath: string;
  header: Uint8Array;
  explanation: string;
};

export type VerifyDeps = {
  /** Full path of the open database file (expo-sqlite's `databasePath`). */
  databasePath: string;
  cipherVersion: string | null;
  /** Reads the first bytes of the file. Wired to expo-file-system's File. */
  readHeader: (path: string, byteCount: number) => Promise<Uint8Array>;
};

/**
 * Check the bytes on disk and explain the verdict.
 *
 * Both halves have to hold: SQLCipher must be present *and* the file must not
 * start with the plaintext magic. The first without the second would mean the
 * pragma silently did nothing.
 */
export async function verifyDatabaseEncryptedOnDisk(
  deps: VerifyDeps,
): Promise<VerificationResult> {
  const header = await deps.readHeader(deps.databasePath, HEADER_BYTES);
  const plaintext = looksLikePlaintextSqlite(header);
  const passed = !plaintext && Boolean(deps.cipherVersion);

  let explanation: string;
  if (plaintext) {
    explanation =
      'FAIL: the file begins with "SQLite format 3\\0", so it is a readable ' +
      'SQLite database. The cache is NOT encrypted at rest.';
  } else if (!deps.cipherVersion) {
    explanation =
      'FAIL: PRAGMA cipher_version returned nothing, so this build is plain ' +
      'SQLite. The header above is not the SQLite magic, which most likely ' +
      'means the file was never written rather than that it is encrypted.';
  } else {
    explanation =
      'PASS: the header is a random SQLCipher salt, not "SQLite format 3\\0". ' +
      'The file is not a readable SQLite database without the key.';
  }

  return {
    passed,
    cipherVersion: deps.cipherVersion,
    databasePath: deps.databasePath,
    header,
    explanation,
  };
}
