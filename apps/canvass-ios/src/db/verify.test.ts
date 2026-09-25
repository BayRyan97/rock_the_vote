import {
  formatHeader,
  HEADER_BYTES,
  looksLikePlaintextSqlite,
  SQLITE_PLAINTEXT_MAGIC,
  verifyDatabaseEncryptedOnDisk,
} from './verify';

/**
 * The exact 16 bytes every unencrypted SQLite file starts with, derived from
 * the ASCII text rather than copied from verify.ts -- so the constant there
 * cannot drift from the real header without a test failing.
 */
const PLAINTEXT_HEADER = Uint8Array.from('SQLite format 3\0', (c) => c.charCodeAt(0));

/** Stand-in for a SQLCipher salt: 16 bytes that are not the magic string. */
const SALT_HEADER = Uint8Array.from([
  0x9f, 0x2a, 0x00, 0xc4, 0x71, 0xe8, 0x13, 0x5d, 0xaa, 0x30, 0x6b, 0xff, 0x02, 0x99, 0x41, 0x7c,
]);

function deps(header: Uint8Array, cipherVersion: string | null = '4.6.1') {
  return {
    databasePath: '/var/mobile/.../SQLite/bellwether-canvass.db',
    cipherVersion,
    readHeader: async (_path: string, n: number) => header.slice(0, n),
  };
}

describe('looksLikePlaintextSqlite', () => {
  it('matches the literal SQLite magic string', () => {
    // Pinned against a Buffer built from the ASCII text, so the hand-written
    // byte array in verify.ts cannot drift from the real header.
    expect(SQLITE_PLAINTEXT_MAGIC).toEqual(PLAINTEXT_HEADER);
    expect(looksLikePlaintextSqlite(PLAINTEXT_HEADER)).toBe(true);
  });

  it('does not match a SQLCipher salt', () => {
    expect(looksLikePlaintextSqlite(SALT_HEADER)).toBe(false);
  });

  it('does not match a header that only shares a prefix', () => {
    const nearMiss = Uint8Array.from(PLAINTEXT_HEADER);
    nearMiss[HEADER_BYTES - 1] = 0x01; // trailing NUL flipped

    expect(looksLikePlaintextSqlite(nearMiss)).toBe(false);
  });

  it('treats a short read as not-plaintext rather than throwing', () => {
    // A truncated read must not be reported as a confident plaintext match.
    expect(looksLikePlaintextSqlite(PLAINTEXT_HEADER.slice(0, 8))).toBe(false);
    expect(looksLikePlaintextSqlite(new Uint8Array(0))).toBe(false);
  });
});

describe('verifyDatabaseEncryptedOnDisk', () => {
  it('passes when the header is a salt and SQLCipher is present', async () => {
    const result = await verifyDatabaseEncryptedOnDisk(deps(SALT_HEADER));

    expect(result.passed).toBe(true);
    expect(result.explanation).toMatch(/^PASS/);
  });

  it('fails loudly when the file is a readable SQLite database', async () => {
    // The failure this whole check exists to catch: a build where
    // `useSQLCipher` was dropped writes a perfectly readable cache.
    const result = await verifyDatabaseEncryptedOnDisk(deps(PLAINTEXT_HEADER));

    expect(result.passed).toBe(false);
    expect(result.explanation).toMatch(/NOT encrypted at rest/);
  });

  it('fails when SQLCipher is absent even if the header is not the magic', async () => {
    // An unwritten or empty file also lacks the magic string. Without the
    // cipher_version check that would read as a pass.
    const result = await verifyDatabaseEncryptedOnDisk(deps(new Uint8Array(16), null));

    expect(result.passed).toBe(false);
    expect(result.explanation).toMatch(/plain\s+SQLite/);
  });

  it('reads exactly the header length, not the whole database', async () => {
    // The file holds voter names. Nothing should pull it into memory.
    const requested: number[] = [];
    await verifyDatabaseEncryptedOnDisk({
      databasePath: '/tmp/x.db',
      cipherVersion: '4.6.1',
      readHeader: async (_p, n) => {
        requested.push(n);
        return SALT_HEADER.slice(0, n);
      },
    });

    expect(requested).toEqual([HEADER_BYTES]);
  });
});

describe('formatHeader', () => {
  it('shows hex and ASCII so a failure is legible on the phone', () => {
    const shown = formatHeader(PLAINTEXT_HEADER);

    expect(shown).toContain('53 51 4c 69');
    expect(shown).toContain('SQLite format 3.');
  });
});
