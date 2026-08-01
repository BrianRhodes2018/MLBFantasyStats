import { useEffect, useState } from 'react'

import { API_BASE } from '../config'
import { fetchData } from '../utils/hitPicksUi'


function formatNumber(value, digits = 2) {
  return value === null || value === undefined ? '—' : Number(value).toFixed(digits)
}


export default function PitcherKsComparisonPage() {
  const [comparison, setComparison] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    fetchData(`${API_BASE}/api/pitcher-ks/compare/latest`)
      .then((data) => {
        if (!cancelled) setComparison(data)
      })
      .catch((err) => {
        if (!cancelled) setError(err.message)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => { cancelled = true }
  }, [])

  const rows = comparison?.rows || []

  return (
    <main className="betting-page pitcher-ks-page">
      <header className="betting-header hit-picks-header pitcher-ks-header">
        <div>
          <span className="hit-history-eyebrow">Strict paired evaluation</span>
          <h2>Pitcher Ks Model Comparison</h2>
          <p className="betting-subtitle">
            All three approaches scored the same frozen probable-starter slate.
          </p>
        </div>
        <span className="pitcher-ks-status-badge">
          {comparison ? comparison.projection_date : 'Loading comparison'}
        </span>
      </header>

      <p className="betting-methodology">
        Model spread is the difference between the highest and lowest expected
        strikeout projection. Large spreads identify pitchers where the K-rate,
        workload, and direct-count assumptions disagree most.
      </p>

      {error && <div className="hit-history-error" role="alert">Could not load comparison: {error}</div>}

      <aside className="pitcher-ks-readiness" aria-label="Comparison readiness">
        <span>Frozen comparison cohort</span>
        <strong>
          {comparison
            ? `${rows.length} paired starters · ${comparison.prediction_window} window`
            : 'Waiting for a complete three-model run'}
        </strong>
        <p>
          {comparison
            ? `As of ${comparison.as_of_timestamp}. Cohort ${comparison.candidate_cohort_id.slice(0, 12)}…`
            : 'Partial or differently timed model runs are never merged into this table.'}
        </p>
      </aside>

      <section className="pitcher-ks-board" aria-labelledby="pitcher-ks-comparison-title">
        <div className="pitcher-ks-board-heading">
          <div>
            <span className="hit-history-eyebrow">Paired model board</span>
            <h3 id="pitcher-ks-comparison-title">Same-slate comparison</h3>
          </div>
          <span className="pitcher-ks-run-state">
            {loading ? 'Loading runs' : `${rows.length} comparable runs`}
          </span>
        </div>

        <div className="hit-picks-table-wrap" aria-busy={loading}>
          <table className="audit-signal-table hit-picks-table hit-compare-table pitcher-ks-table">
            <thead>
              <tr>
                <th>Pitcher</th>
                <th>Matchup</th>
                <th>Approach 1<br />Simulation</th>
                <th>Approach 2<br />Count ML</th>
                <th>Approach 3<br />Bayes</th>
                <th>Model spread</th>
                <th>Projected BF</th>
                <th>Actual Ks</th>
                <th>Evaluation</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={`${row.game_pk}-${row.pitcher_id}`}>
                  <td><strong>{row.pitcher_name}</strong><small>{row.team}</small></td>
                  <td>vs {row.opponent}<small>{row.lineup_source}</small></td>
                  <td>{formatNumber(row.decomposed.projected_ks)}</td>
                  <td>{formatNumber(row.count.projected_ks)}</td>
                  <td>{formatNumber(row.bayes.projected_ks)}</td>
                  <td>{formatNumber(row.model_spread)}</td>
                  <td>{formatNumber(row.decomposed.projected_batters_faced, 1)}</td>
                  <td>{row.actual_ks ?? 'Pending'}</td>
                  <td>{row.actual_ks == null ? 'Awaiting final' : 'Graded'}</td>
                </tr>
              ))}
              {!loading && !rows.length && (
                <tr>
                  <td colSpan="9" className="pitcher-ks-empty-cell">
                    <div className="pitcher-ks-empty">
                      <h3>No paired model runs yet</h3>
                      <p>All three approaches must publish the same cohort before comparison rows appear.</p>
                    </div>
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
    </main>
  )
}
