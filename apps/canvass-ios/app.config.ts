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
 * iOS only, per CLAUDE.md and spec decision #13 (Android dropped from scope
 * 2026-09-21). Expo keeps Android reachable later without a rewrite, but no
 * Android code or configuration belongs in this app.
 */
export default ({ config }: ConfigContext): ExpoConfig => {
  const variant = resolveVariant();

  return {
    ...config,
    name: variant.name,
    // `owner` and `slug` must match the EAS project exactly
    // (https://expo.dev/accounts/thebellwether/projects/thebellwether).
    // `owner` is required because the project lives in the shared
    // organization account, not the personal one.
    owner: 'thebellwether',
    slug: 'thebellwether',
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
      // `useSQLCipher` is what swaps plain SQLite for SQLCipher in the native
      // build (spec §9.2, §11: "Offline cache on a crashed device ->
      // Encrypted SQLite"). Without it, `PRAGMA key` is silently accepted and
      // ignored, and the cache lands on disk in the clear -- which is exactly
      // the failure this task exists to prevent, and it is invisible from JS.
      // `src/db/open.ts` asserts against that at runtime; the flag here is the
      // other half. Changing it requires a new development build, not a
      // reload: it changes native code.
      ['expo-sqlite', { useSQLCipher: true }],
      // Holds the database key in the iOS Keychain (see `src/db/key.ts`).
      'expo-secure-store',
    ],

    experiments: {
      typedRoutes: true,
      reactCompiler: true,
    },

    extra: {
      ...config.extra,
      // Links this config to the EAS project. `eas init` cannot write it
      // itself here -- that only works for a static app.json -- so it is
      // committed by hand. It is an identifier, not a secret.
      eas: {
        projectId: '9902ad20-4faf-4229-a5bd-2d7cc86cbb3c',
      },
      variant: variant.variant,
      apiUrl: variant.apiUrl,
    },
  };
};
