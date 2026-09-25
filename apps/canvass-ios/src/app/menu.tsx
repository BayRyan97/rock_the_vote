import { useState } from 'react';
import { Pressable, ScrollView, StyleSheet } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { formatHeader, verifyEncryptionOnDevice, type VerificationResult } from '@/db';
import { BottomTabInset, Spacing } from '@/constants/theme';

/**
 * Menu tab. The real screen (account, variant, help, sign out) arrives with
 * P1-03; until then it carries P1-04's acceptance check, which can only run on
 * a device -- see `src/db/verify.ts` for why Jest cannot do it.
 *
 * `__DEV__` gates the button so it cannot ship in a preview or production
 * build. It reads 16 bytes of the database file and no voter data.
 */
export default function MenuScreen() {
  const [result, setResult] = useState<VerificationResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);

  async function run() {
    setRunning(true);
    setError(null);
    setResult(null);

    try {
      setResult(await verifyEncryptionOnDevice());
    } catch (e) {
      // A throw here is itself informative: SQLCipherUnavailableError means
      // the build is missing the useSQLCipher plugin option.
      setError(e instanceof Error ? `${e.name}: ${e.message}` : String(e));
    } finally {
      setRunning(false);
    }
  }

  return (
    <ThemedView style={styles.container}>
      <SafeAreaView style={styles.safeArea}>
        <ScrollView contentContainerStyle={styles.content}>
          <ThemedText type="subtitle">Menu</ThemedText>
          <ThemedText type="default" themeColor="textSecondary" style={styles.centered}>
            Account, the active build variant, help, and sign out.
          </ThemedText>
          <ThemedText type="code" themeColor="textSecondary">
            P1-03
          </ThemedText>

          {__DEV__ ? (
            <>
              <ThemedText type="smallBold" style={styles.heading}>
                P1-04 acceptance check
              </ThemedText>
              <ThemedText type="default" themeColor="textSecondary" style={styles.centered}>
                Reads the first 16 bytes of the cache file and checks they are not
                SQLite&apos;s plaintext header.
              </ThemedText>

              <Pressable style={styles.button} onPress={run} disabled={running}>
                <ThemedText type="smallBold">
                  {running ? 'Checking…' : 'Verify encryption at rest'}
                </ThemedText>
              </Pressable>

              {error ? (
                <ThemedText type="code" style={styles.fail}>
                  {error}
                </ThemedText>
              ) : null}

              {result ? (
                <>
                  <ThemedText type="smallBold" style={result.passed ? styles.pass : styles.fail}>
                    {result.passed ? 'PASS' : 'FAIL'}
                  </ThemedText>
                  <ThemedText type="code" themeColor="textSecondary" style={styles.centered}>
                    {formatHeader(result.header)}
                  </ThemedText>
                  <ThemedText type="default" themeColor="textSecondary" style={styles.centered}>
                    {result.explanation}
                  </ThemedText>
                  <ThemedText type="code" themeColor="textSecondary" style={styles.centered}>
                    cipher_version: {result.cipherVersion ?? 'none'}
                  </ThemedText>
                </>
              ) : null}
            </>
          ) : null}
        </ScrollView>
      </SafeAreaView>
    </ThemedView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  safeArea: { flex: 1 },
  content: {
    flexGrow: 1,
    alignItems: 'center',
    justifyContent: 'center',
    gap: Spacing.three,
    paddingHorizontal: Spacing.four,
    paddingBottom: BottomTabInset,
  },
  centered: { textAlign: 'center' },
  heading: { marginTop: Spacing.four },
  button: {
    paddingVertical: Spacing.three,
    paddingHorizontal: Spacing.four,
    borderRadius: 10,
    borderWidth: StyleSheet.hairlineWidth,
  },
  pass: { color: '#1B873F' },
  fail: { color: '#C4314B', textAlign: 'center' },
});
