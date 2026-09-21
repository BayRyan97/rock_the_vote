import { resolveVariant, VARIANTS, type BuildEnv, type Variant } from './variants';

/** A build environment with nothing inherited from the shell running Jest. */
function env(overrides: BuildEnv = {}): BuildEnv {
  return { ...overrides };
}

describe('resolveVariant', () => {
  it('defaults to dev when APP_VARIANT is unset', () => {
    // A developer running `npx expo start` locally sets nothing.
    expect(resolveVariant(env()).variant).toBe('dev');
  });

  it('gives every variant a distinct bundle identifier', () => {
    const ids = VARIANTS.map(
      (variant) =>
        resolveVariant(env({ APP_VARIANT: variant, EXPO_PUBLIC_API_URL: 'https://example.test' }))
          .bundleIdentifier,
    );

    // The point of the whole variant scheme: dev, preview and TestFlight
    // builds coexist on one phone instead of overwriting each other.
    expect(new Set(ids).size).toBe(VARIANTS.length);
    expect(ids).toEqual([
      'org.dlfi.bellwether.canvass.dev',
      'org.dlfi.bellwether.canvass.preview',
      'org.dlfi.bellwether.canvass',
    ]);
  });

  it('gives every variant a distinct name and deep-link scheme', () => {
    const resolved = VARIANTS.map((variant) =>
      resolveVariant(env({ APP_VARIANT: variant, EXPO_PUBLIC_API_URL: 'https://example.test' })),
    );

    expect(new Set(resolved.map((r) => r.name)).size).toBe(VARIANTS.length);
    // Two variants sharing a scheme means iOS picks an arbitrary app for an
    // auth callback -- the bug would surface as a login that lands in the
    // wrong build.
    expect(new Set(resolved.map((r) => r.scheme)).size).toBe(VARIANTS.length);
  });

  it('rejects an unrecognised APP_VARIANT instead of falling back', () => {
    expect(() => resolveVariant(env({ APP_VARIANT: 'staging' }))).toThrow(/not one of/);
    // Case matters: "Production" must not quietly resolve to a dev build.
    expect(() => resolveVariant(env({ APP_VARIANT: 'Production' }))).toThrow(/not one of/);
  });

  it('points dev at the local Next.js app by default', () => {
    expect(resolveVariant(env()).apiUrl).toBe('http://localhost:3000');
  });

  it.each(['preview', 'production'] as Variant[])(
    'refuses to build %s without an explicit API url',
    (variant) => {
      // No default here on purpose. A preview build silently pointed at
      // localhost fails on the phone, hours later, looking like a network bug.
      expect(() => resolveVariant(env({ APP_VARIANT: variant }))).toThrow(/EXPO_PUBLIC_API_URL/);
      expect(() => resolveVariant(env({ APP_VARIANT: variant, EXPO_PUBLIC_API_URL: '   ' }))).toThrow(
        /EXPO_PUBLIC_API_URL/,
      );
    },
  );

  it('lets EXPO_PUBLIC_API_URL override the dev default', () => {
    // How a physical phone reaches the dev server: localhost is the phone.
    const resolved = resolveVariant(
      env({ APP_VARIANT: 'dev', EXPO_PUBLIC_API_URL: 'http://192.168.1.20:3000' }),
    );
    expect(resolved.apiUrl).toBe('http://192.168.1.20:3000');
  });
});
