/**
 * handedness.js — Format player/pitcher names with handedness suffix
 * ===================================================================
 *
 * Appends a handedness tag to a player or pitcher's display name:
 *   - Pitchers: "(RHP)" or "(LHP)" based on throwing hand
 *   - Batters:  "(R)", "(L)", or "(S)" based on batting hand
 *
 * The suffix is omitted when handedness is unknown so older rows or
 * partially-populated data don't render an awkward "(undefined)".
 *
 * Used everywhere a player or pitcher name is displayed (tables, modals,
 * matchups, comparison) so the handedness appears uniformly across the app.
 */

const PITCHER_HAND_LABEL = {
  R: 'RHP',
  L: 'LHP',
}

const BATTER_HAND_LABEL = {
  R: 'R',
  L: 'L',
  S: 'S',
}

/**
 * Format a batter's name with their batting handedness suffix.
 *
 * @param {{name?: string, bats?: string}} player - Object with `name` and `bats` fields
 * @returns {string} e.g. "Aaron Judge (R)" — or just "Aaron Judge" if bats unknown
 */
export function formatBatterName(player) {
  if (!player) return ''
  const name = player.name || ''
  const label = BATTER_HAND_LABEL[player.bats]
  return label ? `${name} (${label})` : name
}

/**
 * Format a pitcher's name with their throwing handedness suffix.
 *
 * @param {{name?: string, throws?: string}} pitcher - Object with `name` and `throws` fields
 * @returns {string} e.g. "Gerrit Cole (RHP)" — or just "Gerrit Cole" if throws unknown
 */
export function formatPitcherName(pitcher) {
  if (!pitcher) return ''
  const name = pitcher.name || ''
  const label = PITCHER_HAND_LABEL[pitcher.throws]
  return label ? `${name} (${label})` : name
}
