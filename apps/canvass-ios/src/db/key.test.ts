import {
  DB_KEY_BYTES,
  DB_KEY_STORE_KEY,
  forgetDatabaseKey,
  getOrCreateDatabaseKey,
  toRawKeyPragmaValue,
  type SecureStoreLike,
} from './key';

/** An in-memory stand-in for the iOS Keychain. */
function fakeStore(initial: Record<string, string> = {}) {
  const items = new Map(Object.entries(initial));
  const calls: { set: number; get: number; delete: number } = { set: 0, get: 0, delete: 0 };

  const store: SecureStoreLike = {
    async getItemAsync(k) {
      calls.get += 1;
      return items.get(k) ?? null;
    },
    async setItemAsync(k, v) {
      calls.set += 1;
      items.set(k, v);
    },
    async deleteItemAsync(k) {
      calls.delete += 1;
      items.delete(k);
    },
  };

  return { store, items, calls };
}

/** Deterministic bytes, so a test can assert the exact pragma string. */
function countingBytes(start = 0): (n: number) => Uint8Array {
  return (n) => Uint8Array.from({ length: n }, (_, i) => (start + i) % 256);
}

describe('toRawKeyPragmaValue', () => {
  it('formats 32 bytes as a raw SQLCipher key, not a passphrase', () => {
    const value = toRawKeyPragmaValue(countingBytes()(DB_KEY_BYTES));

    // The x'...' form tells SQLCipher to use the bytes directly. A bare
    // string would be run through PBKDF2 instead -- still functional, which
    // is why this is pinned rather than left to review.
    expect(value).toMatch(/^x'[0-9a-f]{64}'$/);
    expect(value.startsWith("x'00010203")).toBe(true);
  });

  it('rejects a key of the wrong length instead of padding it', () => {
    // A short key must be a loud failure: SQLCipher would accept a 16-byte
    // hex string as a passphrase and still open the database.
    expect(() => toRawKeyPragmaValue(new Uint8Array(16))).toThrow(/32 bytes/);
    expect(() => toRawKeyPragmaValue(new Uint8Array(64))).toThrow(/32 bytes/);
  });
});

describe('getOrCreateDatabaseKey', () => {
  it('creates a 256-bit key on first run and stores it', async () => {
    const { store, items, calls } = fakeStore();

    const key = await getOrCreateDatabaseKey(store, countingBytes(), {});

    expect(key).toMatch(/^x'[0-9a-f]{64}'$/);
    expect(items.get(DB_KEY_STORE_KEY)).toBe(key);
    expect(calls.set).toBe(1);
  });

  it('reuses the stored key on later runs', async () => {
    const { store, calls } = fakeStore();

    const first = await getOrCreateDatabaseKey(store, countingBytes(1), {});
    const second = await getOrCreateDatabaseKey(store, countingBytes(99), {});

    // Generating a second key would not "reset" anything -- it would orphan
    // the existing database file and every queued outbox event in it.
    expect(second).toBe(first);
    expect(calls.set).toBe(1);
  });

  it('passes the caller"s Keychain options through on both read and write', async () => {
    const seen: object[] = [];
    const store: SecureStoreLike = {
      async getItemAsync(_k, o) {
        seen.push(o ?? {});
        return null;
      },
      async setItemAsync(_k, _v, o) {
        seen.push(o ?? {});
      },
      async deleteItemAsync() {},
    };

    const options = { keychainAccessible: 'WHEN_UNLOCKED_THIS_DEVICE_ONLY' };
    await getOrCreateDatabaseKey(store, countingBytes(), options);

    // THIS_DEVICE_ONLY is what keeps the key out of iCloud Keychain backups
    // (§11). Dropping it on either call would silently weaken that.
    expect(seen).toEqual([options, options]);
  });

  it('asks for exactly 32 bytes of randomness', async () => {
    const { store } = fakeStore();
    const requested: number[] = [];

    await getOrCreateDatabaseKey(
      store,
      (n) => {
        requested.push(n);
        return countingBytes()(n);
      },
      {},
    );

    expect(requested).toEqual([DB_KEY_BYTES]);
  });
});

describe('forgetDatabaseKey', () => {
  it('removes the key so the database can no longer be opened', async () => {
    const { store, items } = fakeStore();
    await getOrCreateDatabaseKey(store, countingBytes(), {});
    expect(items.has(DB_KEY_STORE_KEY)).toBe(true);

    await forgetDatabaseKey(store, {});

    // This is what makes a P1-05 wipe irreversible: the file may survive in
    // free space, but it is ciphertext with no key anywhere on the device.
    expect(items.has(DB_KEY_STORE_KEY)).toBe(false);
  });
});
