import { StyleSheet } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { BottomTabInset, Spacing } from '@/constants/theme';

/**
 * Stand-in for a tab whose real screen is a later task. It names the task that
 * replaces it so an empty tab on the phone is self-explanatory during P1-02
 * rather than looking like a rendering bug.
 */
export function ScreenPlaceholder({ title, task, blurb }: { title: string; task: string; blurb: string }) {
  return (
    <ThemedView style={styles.container}>
      <SafeAreaView style={styles.safeArea}>
        <ThemedText type="subtitle">{title}</ThemedText>
        <ThemedText type="default" themeColor="textSecondary" style={styles.blurb}>
          {blurb}
        </ThemedText>
        <ThemedText type="code" themeColor="textSecondary">
          {task}
        </ThemedText>
      </SafeAreaView>
    </ThemedView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  safeArea: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    gap: Spacing.three,
    paddingHorizontal: Spacing.four,
    paddingBottom: BottomTabInset,
  },
  blurb: { textAlign: 'center' },
});
