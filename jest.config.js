/**
 * Jest for the Next.js app's pure logic and API-contract tests.
 *
 * Deliberately node-environment and ts-jest only: the tests CLAUDE.md asks
 * for first -- lease claim/expiry, outbox idempotency, cache wipe, field
 * visibility, retry/outcome rules, "no scores in API responses" -- are all
 * server-side logic, not React rendering. Add jsdom + testing-library the day
 * a component test actually needs it, not before.
 *
 * The Expo app at apps/ carries its own jest-expo config; it is not a project
 * of this one. See docs/canvass_task_plan.md P1-01.
 */
/** @type {import('jest').Config} */
module.exports = {
  testEnvironment: "node",
  // Whole tree, minus the ignore list below -- so tests can live next to
  // the code (lib/x.test.ts) or in a tests/ dir, without config churn.
  roots: ["<rootDir>"],
  testMatch: ["**/*.test.ts", "**/*.test.tsx"],
  moduleNameMapper: {
    // Mirrors tsconfig.json's "@/*": ["./*"] path alias.
    "^@/(.*)$": "<rootDir>/$1",
  },
  transform: {
    "^.+\.tsx?$": ["ts-jest", { tsconfig: { jsx: "react-jsx" } }],
  },
  // apps/ and services/ are separate projects with their own toolchains,
  // same as model/ and build/.
  testPathIgnorePatterns: ["/node_modules/", "/apps/", "/services/", "/.next/"],
  clearMocks: true,
};
