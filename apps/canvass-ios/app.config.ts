import type { ConfigContext, ExpoConfig } from 'expo/config';

// The explicit `.ts` is required, not a slip. Expo transpiles this file and
// then `require`s it as plain CommonJS, and that require has no TypeScript
// extension resolution -- without the extension it fails with "Cannot find
// module './src/config/variants'". Metro resolves the same import fine, so
// the app code elsewhere does not need this.
import { resolveVariant } from './src/config/variants.ts';

/**
 * Expo config for the Bellwether canvasser app (task plan P1-01, spec §14a).
 *
 * Replaces app.json entirely -- one source of truth, because the variant
 * fields (name, bundle ID, scheme, API origin) are computed, not static.
 *
 * iOS only. CLAUDE.md: no Android code or configuration in this app for now,
 * even though spec §14a describes both platforms eventually.
 */
export default ({ config }: ConfigContext): ExpoConfig => {
  const variant = resolveVariant();

  return {
    ...config,
    name: variant.name,
    slug: 'bellwether-canvass',
    version: '1.0.0',
    // `platforms` is what actually keeps Android out: without it, `expo
    // prebuild` and `eas build` will happily generate an Android project.
    platforms: ['ios'],
    orientation: 'portrait',
    userInterfaceStyle: 'automatic',
    icon: './assets/images/icon.png',
    scheme: variant.scheme,

    ios: {
      bundleIdentifier: variant.bundleIdentifier,
      // Canvassers work from a phone in one hand. No iPad layout to maintain.
      supportsTablet: false,
      infoPlist: {
        // Foreground only -- spec §14a and §11 both rule out a background GPS
        // trail, so there is no NSLocationAlwaysAndWhenInUse key here and
        // there should never be one.
        NSLocationWhenInUseUsageDescription:
          'Bellwether uses your location to plan a walking route from where you are standing and to record where a door was knocked. It never tracks you in the background.',
      },
    },

    plugins: [
      'expo-router',
      [
        'expo-splash-screen',
        {
          backgroundColor: '#208AEF',
          image: './assets/images/splash-icon.png',
          imageWidth: 76,
        },
      ],
    ],

    experiments: {
      typedRoutes: true,
      reactCompiler: true,
    },

    extra: {
      ...config.extra,
      variant: variant.variant,
      apiUrl: variant.apiUrl,
    },
  };
};
