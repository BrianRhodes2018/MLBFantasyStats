import { useEffect, useState } from 'react'

import { API_BASE } from '../config'
import { fetchData, monthKey } from '../utils/hitPicksUi'
import {
  APPROACH_LABELS,
  pitcherResult,
  resultDetail,
  signedError,
} from '../utils/pitcherKsUi'
import { HitPicksCalendar } from './HitPicksPage'
import PitcherKsLegend from './PitcherKsLegend'


function formatNumber(value, digits = 2) {
  return value === null || value === undefined ? '—' : Number(value).toFixed(digits)
}


function approachCell(row, approach) {
  const projection = row[approach]
  return (
    <>
      <strong>{formatNumber(projection.projected_ks)}</strong>
      <small>
        {projection.error === null || projection.error === undefined
          ? 'Awaiting final'
          : `Error ${signedError(projection.error)}`}
      </small>
    </>
  )
}


export default function PitcherKsComparisonPage() {
  const [comparison, setComparison] = useState(null)
  const [dates, setDates] = useState([])
  const [selectedDate, setSelectedDate] = useState(null)
  const [visibleMonth, setVisibleMonth] = useState(monthKey(new Date().toISOString()))
  const [loading, setLoading] = useState(true)
  const [historyLoading, setHistoryLoading] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    Promise.all([
      fetchData(`${API_BASE}/api/pitcher-ks/compare/latest`),
      fetchData(`${API_BASE}/api/pitcher-ks/approaches/decomposed/dates?limit=365`)
        .catch(() => ({ dates: [] })),
    ])
      .then(([latest, history]) => {
        if (cancelled) return
        setComparison(latest)
        setSelectedDate(latest.projection_date)
        setVisibleMonth(monthKey(latest.projection_date))
        setDates(history.dates?.length ? history.dates : [{
          date: latest.projection_date,
          grading_status: latest.evaluation?.complete ? 'graded' : 'pending',
        }])
      })
      .catch((err) => {
        if (!cancelled) setError(err.message)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => { cancelled = true }
  }, [])

  async function loadDate(isoDate) {
    setHistoryLoading(true)
    setError(null)
    try {
      const historical = await fetchData(`${API_BASE}/api/pitcher-ks/compare/${isoDate}`)
      setComparison(historical)
      setSelectedDate(isoDate)
      setVisibleMonth(monthKey(isoDate))
    } catch (err) {
      setError(err.message)
    } finally {
      setHistoryLoading(false)
    }
  }

  const rows = comparison?.rows || []
  const evaluation = comparison?.evaluation || {}
  const approachMetrics = evaluation.approaches || {}
  const latestDate = dates[0]?.date || comparison?.projection_date
  const bestLabels = (evaluation.best_approaches || [])
    .map((approach) => APPROACH_LABELS[approach])
    .join(' / ')

  return (
    <main className="betting-page pitcher-ks-page">
      {comparison && (
        <HitPicksCalendar
          dates={dates}
          selectedDate={selectedDate}
          visibleMonth={visibleMonth}
          onMonthChange={setVisibleMonth}
          onSelectDate={loadDate}
          latestDate={latestDate}
          loading={historyLoading}
          ariaLabel="Pitcher Ks comparison history calendar"
          eyebrow="Comparison history"
          title="Select a game date"
          itemLabel="Pitcher Ks comparisons"
        />
      )}

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
        strikeout projection. Each model’s signed error is Projected Ks minus Actual Ks.
      </p>
      <PitcherKsLegend />

      {error && <div className="hit-history-error" role="alert">Could not load comparison: {error}</div>}

      <aside className="pitcher-ks-readiness" aria-label="Comparison readiness">
        <span>{evaluation.complete ? 'Final paired evaluation' : 'Frozen comparison cohort'}</span>
        <strong>
          {comparison
            ? `${rows.length} paired starters · ${comparison.prediction_window} window`
            : 'Waiting for a complete three-model run'}
        </strong>
        <p>
          {comparison
            ? `As of ${comparison.as_of_timestamp}. Cohort ${comparison.candidate_cohort_id.slice(0, 12)}… ${evaluation.complete ? 'All eligible results are final.' : 'Metrics remain provisional until every eligible game is resolved.'}`
            : 'Partial or differently timed model runs are never merged into this table.'}
        </p>
      </aside>

      {evaluation.graded_starters > 0 && (
        <section className="pitcher-ks-metric-grid" aria-label="Live model error comparison">
          {['decomposed', 'count', 'bayes'].map((approach) => (
            <article key={approach}>
              <span>{APPROACH_LABELS[approach]}</span>
              <strong>MAE {formatNumber(approachMetrics[approach]?.mae)} Ks</strong>
              <small>
                RMSE {formatNumber(approachMetrics[approach]?.rmse)} · Bias {signedError(approachMetrics[approach]?.bias)}
              </small>
            </article>
          ))}
          <article className="pitcher-ks-best-model">
            <span>{evaluation.complete ? 'Best final MAE' : 'Provisional best MAE'}</span>
            <strong>{bestLabels || 'Awaiting results'}</strong>
            <small>{evaluation.graded_starters} confirmed starters</small>
          </article>
        </section>
      )}

      <section className="pitcher-ks-board" aria-labelledby="pitcher-ks-comparison-title">
        <div className="pitcher-ks-board-heading">
          <div>
            <span className="hit-history-eyebrow">Paired model board</span>
            <h3 id="pitcher-ks-comparison-title">Same-slate comparison</h3>
          </div>
          <span className="pitcher-ks-run-state">
            {loading ? 'Loading runs' : `${rows.length} comparable pitchers`}
          </span>
        </div>

        <div className="hit-picks-table-wrap" aria-busy={loading || historyLoading}>
          {historyLoading && <div className="hit-history-loading">Loading date…</div>}
          <table className="audit-signal-table hit-picks-table hit-compare-table pitcher-ks-table">
            <thead>
              <tr>
                <th>Pitcher</th>
                <th>Matchup</th>
                <th>Approach 1<br />Simulation</th>
                <th>Approach 2<br />Count ML</th>
                <th>Approach 3<br />Bayes</th>
                <th>Model spread</th>
                <th>Actual Ks</th>
                <th>Closest model</th>
                <th>Result</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const result = pitcherResult(row)
                const closest = (row.closest_approaches || [])
                  .map((approach) => APPROACH_LABELS[approach])
                  .join(' / ')
                return (
                  <tr key={`${row.game_pk}-${row.pitcher_id}`}>
                    <td><strong>{row.pitcher_name}</strong><small>{row.team}</small></td>
                    <td>vs {row.opponent}<small>{row.lineup_source}</small></td>
                    <td>{approachCell(row, 'decomposed')}</td>
                    <td>{approachCell(row, 'count')}</td>
                    <td>{approachCell(row, 'bayes')}</td>
                    <td>{formatNumber(row.model_spread)}</td>
                    <td>
                      {row.actual_ks ?? '—'}
                      {row.actual_ks !== null && row.actual_ks !== undefined && (
                        <small>{row.actual_batters_faced ?? '—'} BF · {formatNumber(row.actual_innings_pitched, 1)} IP</small>
                      )}
                    </td>
                    <td>{closest || '—'}</td>
                    <td>
                      <span
                        className={`pitcher-k-result ${result.className}`}
                        title={resultDetail(row)}
                        aria-label={`${result.text}: ${resultDetail(row)}`}
                      >
                        {result.text}
                      </span>
                    </td>
                  </tr>
                )
              })}
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

      <p className="betting-methodology pitcher-ks-result-methodology">
        A closest model is shown only for confirmed starters. DNS results are excluded,
        and the daily winner remains provisional until the full frozen cohort is resolved.
      </p>
    </main>
  )
}
