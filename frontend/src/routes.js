export function viewFromPath(pathname, pitcherKsEnabled = false) {
  if (pitcherKsEnabled) {
    if (pathname === '/pitcher-ks/count-model') return 'pitcherks-count'
    if (pathname === '/pitcher-ks/empirical-bayes') return 'pitcherks-bayes'
    if (pathname === '/pitcher-ks/compare') return 'pitcherks-compare'
    if (pathname === '/pitcher-ks/decomposed' || pathname === '/pitcher-ks') {
      return 'pitcherks-decomposed'
    }
  }
  if (pathname === '/hit-picks/v3') return 'hitpicks-v3'
  if (pathname === '/hit-picks/compare') return 'hitpicks-compare'
  if (pathname === '/hit-picks/v2' || pathname === '/hit-picks') {
    return 'hitpicks-v2'
  }
  return 'dashboard'
}
