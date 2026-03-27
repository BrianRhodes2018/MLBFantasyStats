/**
 * fuzzyMatch.js — Shared fuzzy name matching utilities
 * =====================================================
 *
 * Extracted from App.jsx for reuse across components.
 * Used by: App.jsx (table name search), PlayerComparison.jsx (autocomplete)
 *
 * Two main exports:
 *   - fuzzyNameMatch(query, name) → boolean  (does it match?)
 *   - fuzzyMatchScore(query, name) → number  (how well does it match?)
 *
 * The score function enables RANKING results so the closest matches
 * appear first, rather than just filtering in database order.
 *
 * Score tiers (lower = better match):
 *   0 = exact full-name match        ("aaron judge" → "Aaron Judge")
 *   1 = name starts with query        ("aaron" → "Aaron Judge")
 *   2 = exact word match              ("judge" → "Aaron Judge")
 *   3 = word starts with query        ("jud" → "Aaron Judge")
 *   4 = substring match               ("udge" → "Aaron Judge")
 *   5+ = Levenshtein fuzzy match      ("juge" → "Aaron Judge", score = 5 + edit distance)
 *   Infinity = no match
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
 * Compute a match score between a search query and a player name.
 * Lower score = better match. Returns Infinity for no match.
 *
 * This is the scoring version of fuzzyNameMatch — it uses the same
 * matching logic but returns a numeric score instead of a boolean,
 * enabling results to be sorted by relevance.
 *
 * @param {string} query - The user's search text
 * @param {string} name - The player's full name
 * @returns {number} Match score (lower = better, Infinity = no match)
 */
export const fuzzyMatchScore = (query, name) => {
  const q = query.toLowerCase().trim()
  const n = name.toLowerCase()

  if (!q) return 0

  // Tier 0: Exact full-name match — best possible score
  // "aaron judge" matches "Aaron Judge" exactly
  if (n === q) return 0

  // Tier 1: Full name starts with query
  // "aaron" matches "Aaron Judge" — the user is typing the name from the start
  if (n.startsWith(q)) return 1

  const nameWords = n.split(/\s+/)

  // Tier 2: Exact match on any word (first name, last name, etc.)
  // "judge" exactly matches the last-name word in "Aaron Judge"
  for (const word of nameWords) {
    if (word === q) return 2
  }

  // Tier 3: A word starts with the query — strong prefix match
  // "jud" matches the start of "judge" in "Aaron Judge"
  for (const word of nameWords) {
    if (word.startsWith(q)) return 3
  }

  // Tier 4: Substring match anywhere in the full name
  // "udge" is found inside "Aaron Judge"
  if (n.includes(q)) return 4

  // Tier 5+: Fuzzy matching via Levenshtein distance
  // Handles typos: "juge" is 1 edit from "judge", scored as 5 + 1 = 6
  const maxDist = q.length <= 3 ? 1 : q.length <= 6 ? 2 : 3
  let bestDist = Infinity

  for (const word of nameWords) {
    const dist = levenshtein(q, word)
    if (dist <= maxDist && dist < bestDist) {
      bestDist = dist
    }

    // Also check prefix fuzzy match for shorter queries
    if (q.length < word.length) {
      const prefix = word.substring(0, q.length)
      const prefixDist = levenshtein(q, prefix)
      if (prefixDist <= Math.max(1, Math.floor(q.length / 3)) && prefixDist < bestDist) {
        bestDist = prefixDist
      }
    }
  }

  if (bestDist < Infinity) return 5 + bestDist

  return Infinity
}

/**
 * Fuzzy match a search query against a player name.
 *
 * @param {string} query - The user's search text
 * @param {string} name - The player's full name
 * @returns {boolean} True if the name fuzzy-matches the query
 */
export const fuzzyNameMatch = (query, name) => {
  return fuzzyMatchScore(query, name) < Infinity
}
