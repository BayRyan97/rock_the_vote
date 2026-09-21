/**
 * Jest for the canvasser app. Separate from the repo-root Jest config: that
 * one is node-environment ts-jest for the Next.js server logic, and apps/ is
 * explicitly ignored there (see ../../jest.config.js).
 *
 * jest-expo gives the React Native module mocks and the Metro-compatible
 * transform, so the same preset covers the pure config tests here and the
 * component tests that arrive with P1-09/P1-10.
 */
/** @type {import('jest').Config} */
module.exports = {
  preset: 'jest-expo/ios',
  roots: ['<rootDir>/src', '<rootDir>/__tests__'],
  moduleNameMapper: {
    // Mirrors tsconfig.json's path aliases.
    '^@/assets/(.*)$': '<rootDir>/assets/$1',
    '^@/(.*)$': '<rootDir>/src/$1',
  },
  clearMocks: true,
};
