/**
 * fuzzyMatch.js — Shared fuzzy name matching utilities
 * =====================================================
 *
 * Extracted from App.jsx for reuse across components.
 * Used by: App.jsx (table name search), PlayerComparison.jsx (autocomplete)
 */

/**
 * Compute the Levenshtein (edit) distance between two strings.
 *
 * @param {string} a - First string
 * @param {string} b - Second string
 * @returns {number} The edit distance (0 = identical, higher = more different)
 */
export const levenshtein = (a, b) => {
  if (a.length === 0) return b.length
  if (b.length === 0) return a.length

  const matrix = []
  for (let i = 0; i <= a.length; i++) matrix[i] = [i]
  for (let j = 0; j <= b.length; j++) matrix[0][j] = j

  for (let i = 1; i <= a.length; i++) {
    for (let j = 1; j <= b.length; j++) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1
      matrix[i][j] = Math.min(
        matrix[i - 1][j] + 1,
        matrix[i][j - 1] + 1,
        matrix[i - 1][j - 1] + cost
      )
    }
  }
  return matrix[a.length][b.length]
}

/**
 * Fuzzy match a search query against a player name.
 *
 * @param {string} query - The user's search text
 * @param {string} name - The player's full name
 * @returns {boolean} True if the name fuzzy-matches the query
 */
export const fuzzyNameMatch = (query, name) => {
  const q = query.toLowerCase().trim()
  const n = name.toLowerCase()

  if (!q) return true

  if (n.includes(q)) return true

  const nameWords = n.split(/\s+/)
  const maxDist = q.length <= 3 ? 1 : q.length <= 6 ? 2 : 3

  for (const word of nameWords) {
    if (levenshtein(q, word) <= maxDist) return true

    if (q.length < word.length) {
      const prefix = word.substring(0, q.length)
      if (levenshtein(q, prefix) <= Math.max(1, Math.floor(q.length / 3))) return true
    }
  }

  return false
}
