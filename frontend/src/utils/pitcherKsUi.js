export const APPROACH_LABELS = {
  decomposed: 'Simulation',
  count: 'Count ML',
  bayes: 'Empirical Bayes',
}


export function pitcherResult(prediction) {
  const status = prediction?.result_status
    || (prediction?.actual_ks !== null && prediction?.actual_ks !== undefined
      ? 'graded'
      : 'pending')
  const labels = {
    pending: ['Pending', 'pitcher-k-result-pending'],
    graded: ['Final', 'pitcher-k-result-graded'],
    did_not_start: ['DNS', 'pitcher-k-result-dns'],
    postponed: ['Postponed', 'pitcher-k-result-pending'],
    suspended: ['Suspended', 'pitcher-k-result-pending'],
    cancelled: ['Cancelled', 'pitcher-k-result-dns'],
    data_unavailable: ['Data unavailable', 'pitcher-k-result-pending'],
  }
  const [text, className] = labels[status] || labels.pending
  return { status, text, className }
}


export function signedError(value) {
  if (value === null || value === undefined) return '—'
  const numeric = Number(value)
  return `${numeric > 0 ? '+' : ''}${numeric.toFixed(2)}`
}


export function resultDetail(prediction) {
  const result = pitcherResult(prediction)
  if (prediction?.grade_detail) return prediction.grade_detail
  if (result.status === 'graded') return 'Confirmed from the official final MLB pitching line.'
  if (result.status === 'did_not_start') return 'The projected probable pitcher did not start this game.'
  return 'Waiting for an official final game result.'
}
