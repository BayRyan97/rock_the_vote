import type { ConfigContext, ExpoConfig } from 'expo/config';

import defineConfig from '../app.config';
import { VARIANTS } from '@/config/variants';

/**
 * Covers app.config.ts itself, not just the pure resolver underneath it --
 * the wiring between the two has already broken once (Expo's config loader
 * needs the explicit `.ts` on the import, see the comment in app.config.ts).
 */

// `config` is whatever static config Expo found; there is no app.json here, so
// it arrives near-empty.
const context = { config: { name: '', slug: '' } } as unknown as ConfigContext;

function configFor(variant: string): ExpoConfig {
  const previous = { ...process.env };
  try {
    process.env.APP_VARIANT = variant;
    process.env.EXPO_PUBLIC_API_URL = 'https://example.test';
    return defineConfig(context);
  } finally {
    process.env = previous;
  }
}

describe('app.config.ts', () => {
  it('resolves three distinct bundle identifiers', () => {
    const ids = VARIANTS.map((variant) => configFor(variant).ios?.bundleIdentifier);

    expect(ids.every(Boolean)).toBe(true);
    expect(new Set(ids).size).toBe(3);
  });

  it('stays iOS only', () => {
    const config = configFor('production');

    // CLAUDE.md: no Android configuration in this app. Without `platforms`,
    // `expo prebuild` and `eas build` generate an Android project anyway.
    expect(config.platforms).toEqual(['ios']);
    expect(config.android).toBeUndefined();
  });

  it('asks for foreground location only', () => {
    const infoPlist = configFor('production').ios?.infoPlist ?? {};

    expect(infoPlist.NSLocationWhenInUseUsageDescription).toEqual(expect.any(String));
    // Spec §11 and §14a both rule out a background GPS trail. If one of these
    // keys ever appears, the App Store privacy labels (R-02) are wrong too.
    expect(infoPlist.NSLocationAlwaysAndWhenInUseUsageDescription).toBeUndefined();
    expect(infoPlist.NSLocationAlwaysUsageDescription).toBeUndefined();
    expect(config_backgroundModes(infoPlist)).not.toContain('location');
  });

  it('exposes the resolved variant and API origin to the app', () => {
    const config = configFor('preview');

    expect(config.extra?.variant).toBe('preview');
    expect(config.extra?.apiUrl).toBe('https://example.test');
  });
});

function config_backgroundModes(infoPlist: Record<string, unknown>): string[] {
  const modes = (infoPlist.UIBackgroundModes ?? []) as unknown;
  return Array.isArray(modes) ? modes : [];
}
