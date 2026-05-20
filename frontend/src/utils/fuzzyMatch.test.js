import { describe, expect, it } from 'vitest'
import { fuzzyNameMatch, fuzzyMatchScore, levenshtein } from './fuzzyMatch'

describe('fuzzyMatch utilities', () => {
  it('computes edit distance', () => {
    expect(levenshtein('judge', 'jude')).toBe(1)
    expect(levenshtein('mookie', 'mookie')).toBe(0)
  })

  it('scores exact, prefix, and typo matches ahead of misses', () => {
    expect(fuzzyMatchScore('aaron judge', 'Aaron Judge')).toBe(0)
    expect(fuzzyMatchScore('aar', 'Aaron Judge')).toBeLessThan(fuzzyMatchScore('juge', 'Aaron Judge'))
    expect(fuzzyMatchScore('zzzz', 'Aaron Judge')).toBe(Infinity)
  })

  it('returns a boolean match result for table filters', () => {
    expect(fuzzyNameMatch('soto', 'Juan Soto')).toBe(true)
    expect(fuzzyNameMatch('zzzz', 'Juan Soto')).toBe(false)
  })
})
