// https://docs.expo.dev/guides/using-eslint/
const { defineConfig } = require('eslint/config');
const expoConfig = require('eslint-config-expo/flat');

module.exports = defineConfig([
  expoConfig,
  {
    ignores: ['dist/*', 'src/components/**', 'src/hooks/**', 'src/constants/theme.ts'], // unused Expo starter code (see tsconfig)
  },
]);
