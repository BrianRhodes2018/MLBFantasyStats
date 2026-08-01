import { describe, expect, it } from 'vitest'

import { viewFromPath } from './routes'


describe('Pitcher Ks route gate', () => {
  it.each([
    '/pitcher-ks',
    '/pitcher-ks/decomposed',
    '/pitcher-ks/count-model',
    '/pitcher-ks/empirical-bayes',
    '/pitcher-ks/compare',
  ])('keeps %s dark when the release flag is disabled', (pathname) => {
    expect(viewFromPath(pathname, false)).toBe('dashboard')
  })

  it('resolves Pitcher Ks routes when the release flag is enabled', () => {
    expect(viewFromPath('/pitcher-ks/decomposed', true)).toBe('pitcherks-decomposed')
    expect(viewFromPath('/pitcher-ks/count-model', true)).toBe('pitcherks-count')
    expect(viewFromPath('/pitcher-ks/empirical-bayes', true)).toBe('pitcherks-bayes')
    expect(viewFromPath('/pitcher-ks/compare', true)).toBe('pitcherks-compare')
  })

  it('does not alter existing Hit Picks routing', () => {
    expect(viewFromPath('/hit-picks/v2', false)).toBe('hitpicks-v2')
    expect(viewFromPath('/hit-picks/v3', false)).toBe('hitpicks-v3')
    expect(viewFromPath('/hit-picks/compare', false)).toBe('hitpicks-compare')
  })
})
