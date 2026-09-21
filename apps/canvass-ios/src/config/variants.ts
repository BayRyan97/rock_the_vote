/**
 * Build variants for the canvasser app (task plan P1-01, spec §14a).
 *
 * Three variants, each with its own bundle identifier and its own backend, so
 * a dev build and a TestFlight build can sit on the same phone without one
 * overwriting the other and without either pointing at the wrong Supabase
 * project. `APP_VARIANT` selects one; `eas.json` sets it per build profile.
 *
 * This file is deliberately pure -- it takes an env bag and returns a plain
 * object -- so `variants.test.ts` can assert the three bundle IDs really are
 * distinct without booting Expo's config loader.
 */

export const VARIANTS = ['dev', 'preview', 'production'] as const;

export type Variant = (typeof VARIANTS)[number];

export type VariantConfig = {
  variant: Variant;
  /** Home-screen name. Kept short: iOS truncates past ~12 characters. */
  name: string;
  bundleIdentifier: string;
  /** Deep-link scheme, used by the auth callback (P1-03). */
  scheme: string;
  /** Bellwether API origin. All voter reads go through it, never straight to Supabase (§11). */
  apiUrl: string;
};

/**
 * The reverse-DNS prefix, chosen 2026-09-21. This has to match the identifier
 * registered under the individual Apple Developer account (task S-01), and
 * changing it after the first TestFlight build means a new App Store Connect
 * app record -- so it is settled here, before P1-02, rather than after.
 *
 * Apple does not require the prefix to match a domain anyone owns. The
 * `.canvass` segment leaves room for other Bellwether apps (for example
 * `org.bellwether.dashboard`) under the same prefix.
 */
const BUNDLE_PREFIX = 'org.bellwether.canvass';

type VariantDefaults = Omit<VariantConfig, 'apiUrl'> & { apiUrl: string | null };

const DEFAULTS: Record<Variant, VariantDefaults> = {
  dev: {
    variant: 'dev',
    name: 'Bellwether Dev',
    bundleIdentifier: `${BUNDLE_PREFIX}.dev`,
    scheme: 'bellwether-canvass-dev',
    // `npx expo start --dev-client` against the local Next.js app. On a
    // physical phone this must be the host machine's LAN address instead, so
    // EXPO_PUBLIC_API_URL overrides it.
    apiUrl: 'http://localhost:3000',
  },
  preview: {
    variant: 'preview',
    name: 'Bellwether Pre',
    bundleIdentifier: `${BUNDLE_PREFIX}.preview`,
    scheme: 'bellwether-canvass-preview',
    // No default on purpose: a preview build silently pointed at localhost
    // looks like a network bug on the phone, hours later.
    apiUrl: null,
  },
  production: {
    variant: 'production',
    name: 'Bellwether',
    bundleIdentifier: BUNDLE_PREFIX,
    scheme: 'bellwether-canvass',
    apiUrl: null,
  },
};

export function isVariant(value: unknown): value is Variant {
  return typeof value === 'string' && (VARIANTS as readonly string[]).includes(value);
}

/**
 * Just the two variables this reads, rather than `NodeJS.ProcessEnv` -- Expo
 * augments that type with a required NODE_ENV, which would force every test to
 * build a fake environment wider than the thing under test.
 */
export type BuildEnv = {
  APP_VARIANT?: string;
  EXPO_PUBLIC_API_URL?: string;
  // The index signature is what lets a real `process.env` be passed in: an
  // all-optional type is a "weak type" to TypeScript, and it rejects an
  // argument that shares none of its named properties.
  [key: string]: string | undefined;
};

/**
 * Resolve the active variant from the environment.
 *
 * Throws rather than falling back, in both failure cases. An unrecognised
 * `APP_VARIANT` that quietly became `dev` would ship a build named "Bellwether
 * Dev" to TestFlight; a missing API URL would ship one that cannot log in.
 * Both are cheap to catch here and expensive to catch on a device.
 */
export function resolveVariant(env: BuildEnv = process.env): VariantConfig {
  const requested = env.APP_VARIANT ?? 'dev';

  if (!isVariant(requested)) {
    throw new Error(
      `APP_VARIANT="${requested}" is not one of: ${VARIANTS.join(', ')}. ` +
        'Set it in the eas.json build profile, or leave it unset for dev.',
    );
  }

  const defaults = DEFAULTS[requested];
  const apiUrl = env.EXPO_PUBLIC_API_URL?.trim() || defaults.apiUrl;

  if (!apiUrl) {
    throw new Error(
      `EXPO_PUBLIC_API_URL must be set for the "${requested}" variant. ` +
        'Add it to the EAS environment for this profile (eas env:set).',
    );
  }

  return { ...defaults, apiUrl };
}
