/**
 * Metro infers this preset on its own, but Jest does not: jest-expo runs the
 * project's Babel config, and without it the Flow annotations in React
 * Native's own jest setup fail to parse.
 */
module.exports = function (api) {
  api.cache(true);
  return { presets: ['babel-preset-expo'] };
};
