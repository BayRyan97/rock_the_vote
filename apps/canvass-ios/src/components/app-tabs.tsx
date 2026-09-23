import { NativeTabs } from 'expo-router/unstable-native-tabs';

import { useTheme } from '@/hooks/use-theme';

/**
 * The four canvasser tabs from spec §4, in shift order: the turf you are
 * working (§4.5), the door you are walking to (§4.2), how the shift is going
 * (§4.6), and everything else.
 *
 * SF Symbols rather than bundled PNGs -- this app is iOS only, so the system
 * icons are available and stay correct at every scale and weight.
 */
export default function AppTabs() {
  const theme = useTheme();

  return (
    <NativeTabs
      backgroundColor={theme.background}
      indicatorColor={theme.backgroundElement}
      labelStyle={{ selected: { color: theme.text } }}>
      <NativeTabs.Trigger name="index">
        <NativeTabs.Trigger.Icon sf="map" />
        <NativeTabs.Trigger.Label>My List</NativeTabs.Trigger.Label>
      </NativeTabs.Trigger>

      <NativeTabs.Trigger name="next-door">
        <NativeTabs.Trigger.Icon sf="figure.walk" />
        <NativeTabs.Trigger.Label>Next Door</NativeTabs.Trigger.Label>
      </NativeTabs.Trigger>

      <NativeTabs.Trigger name="progress">
        <NativeTabs.Trigger.Icon sf="chart.bar" />
        <NativeTabs.Trigger.Label>Progress</NativeTabs.Trigger.Label>
      </NativeTabs.Trigger>

      <NativeTabs.Trigger name="menu">
        <NativeTabs.Trigger.Icon sf="line.3.horizontal" />
        <NativeTabs.Trigger.Label>Menu</NativeTabs.Trigger.Label>
      </NativeTabs.Trigger>
    </NativeTabs>
  );
}
