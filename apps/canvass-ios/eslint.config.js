// Flat config for the canvasser app. Separate from the repo-root config,
// which ignores apps/** -- the rules there are Next.js rules and do not apply
// to React Native.
const { defineConfig } = require('eslint/config');
const expoConfig = require('eslint-config-expo/flat');

module.exports = defineConfig([
  expoConfig,
  {
    ignores: ['dist/*', 'node_modules/*', '.expo/*', 'expo-env.d.ts'],
  },
]);
