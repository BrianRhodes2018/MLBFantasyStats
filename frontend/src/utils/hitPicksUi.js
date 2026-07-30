export function formatPct(value, digits = 1) {
  if (value === null || value === undefined) return '-'
  return `${(value * 100).toFixed(digits)}%`
}

export function formatStatline(pick) {
  if (pick.played === null || pick.played === undefined) {
    return 'Awaiting final box score'
  }
  if (!pick.played) return 'Did not play'

  const parts = [`${pick.hits ?? 0}-for-${pick.at_bats ?? '?'}`]
  if ((pick.doubles ?? 0) > 0) parts.push(`${pick.doubles} 2B`)
  if ((pick.triples ?? 0) > 0) parts.push(`${pick.triples} 3B`)
  if ((pick.home_runs ?? 0) > 0) parts.push(`${pick.home_runs} HR`)
  if ((pick.runs ?? 0) > 0) parts.push(`${pick.runs} R`)
  if ((pick.rbi ?? 0) > 0) parts.push(`${pick.rbi} RBI`)
  if ((pick.walks ?? 0) > 0) parts.push(`${pick.walks} BB`)
  if ((pick.strikeouts ?? 0) > 0) parts.push(`${pick.strikeouts} K`)
  if (pick.total_bases !== null && pick.total_bases !== undefined) {
    parts.push(`${pick.total_bases} TB`)
  }
  return parts.join(', ')
}

export function resultLabel(pick) {
  if (pick.played === null || pick.played === undefined) {
    return { text: 'Pending', className: 'hit-result-pending' }
  }
  if (!pick.played) return { text: 'DNP', className: 'hit-result-dnp' }
  if (pick.got_hit) return { text: 'Hit', className: 'hit-result-hit' }
  return { text: 'Miss', className: 'hit-result-miss' }
}

export async function fetchData(url) {
  const response = await fetch(url)
  if (!response.ok) {
    const detail = (await response.json().catch(() => null))?.detail
    throw new Error(detail || `Request failed (${response.status})`)
  }
  return (await response.json()).data
}

export function monthKey(isoDate) {
  return isoDate.slice(0, 7)
}
