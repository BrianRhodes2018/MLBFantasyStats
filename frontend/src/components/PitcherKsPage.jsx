import { useEffect, useState } from 'react'

import { API_BASE } from '../config'
import { fetchData, formatPct, monthKey } from '../utils/hitPicksUi'
import { pitcherResult, resultDetail, signedError } from '../utils/pitcherKsUi'
import { HitPicksCalendar } from './HitPicksPage'
import PitcherKsLegend from './PitcherKsLegend'


const APPROACHES = {
  decomposed: {
    eyebrow: 'Approach 1 · decomposed model',
    title: 'Decomposed Matchup + Workload Simulation',
    subtitle: 'A learned K/BF component and a separate batters-faced model combined into one count distribution.',
    methodology: 'Gradient-boosted strikeout-rate and workload models are trained independently, then combined with a beta-binomial workload mixture. This keeps a short leash from being mistaken for weak strikeout skill.',
  },
  count: {
    eyebrow: 'Approach 2 · direct ML model',
    title: 'Direct Count + Quantile Model',
    subtitle: 'Poisson mean and quantile gradient boosting trained directly on starter-game strikeout totals.',
    methodology: 'One model predicts the expected count while two chronological quantile models estimate its conditional spread. Their output is converted into a coherent discrete strikeout distribution.',
  },
  bayes: {
    eyebrow: 'Approach 3 · transparent baseline',
    title: 'Empirical-Bayes Matchup Baseline',
    subtitle: 'Shrunk pitcher and opponent strikeout rates mixed over projected workload.',
    methodology: 'Pitcher, recent-form, projected-lineup, opponent-team, and league rates are regressed toward stable priors before being mixed over a batters-faced distribution.',
  },
}


function formatNumber(value, digits = 1) {
  return value === null || value === undefined ? '—' : Number(value).toFixed(digits)
}


function formatTimestamp(value) {
  if (!value) return 'As-of time unavailable'
  return new Intl.DateTimeFormat('en-US', {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
    timeZoneName: 'short',
  }).format(new Date(value))
}


export default function PitcherKsPage({ approach = 'decomposed' }) {
  const config = APPROACHES[approach] || APPROACHES.decomposed
  const [board, setBoard] = useState(null)
  const [ledger, setLedger] = useState(null)
  const [historyDates, setHistoryDates] = useState([])
  const [selectedDate, setSelectedDate] = useState(null)
  const [visibleMonth, setVisibleMonth] = useState(monthKey(new Date().toISOString()))
  const [loading, setLoading] = useState(true)
  const [historyLoading, setHistoryLoading] = useState(false)
  const [error, setError] = useState(null)
  const [historyError, setHistoryError] = useState(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)

    Promise.all([
      fetchData(`${API_BASE}/api/pitcher-ks/approaches/${approach}/latest`),
      fetchData(`${API_BASE}/api/pitcher-ks/approaches/${approach}/dates?limit=365`)
        .catch(() => ({ dates: [], latest_date: null })),
      fetchData(`${API_BASE}/api/pitcher-ks/ledger`).catch(() => null),
    ])
      .then(([latest, history, trackRecord]) => {
        if (cancelled) return
        setBoard(latest)
        setLedger(trackRecord)
        setSelectedDate(latest.projection_date)
        setVisibleMonth(monthKey(latest.projection_date))
        setHistoryDates(history.dates?.length ? history.dates : [{
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
  }, [approach])

  async function loadHistoricalDate(isoDate) {
    setHistoryLoading(true)
    setHistoryError(null)
    try {
      const historical = await fetchData(
        `${API_BASE}/api/pitcher-ks/approaches/${approach}/${isoDate}`,
      )
      setBoard(historical)
      setSelectedDate(isoDate)
      setVisibleMonth(monthKey(isoDate))
    } catch (err) {
      setHistoryError(err.message)
    } finally {
      setHistoryLoading(false)
    }
  }

  const metrics = board?.backtest || {}
  const evaluation = board?.evaluation || {}
  const liveRecord = ledger?.approaches?.[approach]
  const predictions = board?.approach === approach ? board.predictions || [] : []
  const latestDate = historyDates[0]?.date || board?.projection_date

  return (
    <main className="betting-page pitcher-ks-page">
      {board && (
        <HitPicksCalendar
          dates={historyDates}
          selectedDate={selectedDate}
          visibleMonth={visibleMonth}
          onMonthChange={setVisibleMonth}
          onSelectDate={loadHistoricalDate}
          latestDate={latestDate}
          loading={historyLoading}
          ariaLabel="Pitcher Ks projection history calendar"
          eyebrow="Projection history"
          title="Select a game date"
          itemLabel="Pitcher Ks projections"
        />
      )}

      <header className="betting-header hit-picks-header pitcher-ks-header">
        <div>
          <span className="hit-history-eyebrow">{config.eyebrow}</span>
          <h2>{config.title}</h2>
          <p className="betting-subtitle">{config.subtitle}</p>
        </div>
        <span className="pitcher-ks-status-badge">
          {board?.model_version || 'Model loading'}
        </span>
      </header>

      <p className="betting-methodology">{config.methodology}</p>
      <PitcherKsLegend />

      {error && (
        <div className="hit-history-error" role="alert">
          Could not load Pitcher Ks: {error}
        </div>
      )}
      {historyError && (
        <div className="hit-history-error" role="alert">
          Could not load that date: {historyError}
        </div>
      )}

      <div className="pitcher-ks-summary-grid">
        <aside className="pitcher-ks-readiness" aria-label="Model validation summary">
          <span>Chronological validation</span>
          <strong>
            {metrics.starts
              ? `${metrics.starts.toLocaleString()} held-out starts · MAE ${formatNumber(metrics.mae, 2)} Ks`
              : 'Waiting for a published model run'}
          </strong>
          <p>
            {metrics.starts
              ? `RMSE ${formatNumber(metrics.rmse, 2)}, bias ${formatNumber(metrics.bias, 2)}, 80% interval coverage ${formatPct(metrics.interval_80_coverage)}. Trained through ${board.trained_through} on ${board.trained_on_rows.toLocaleString()} starter-games.`
              : 'The scorer publishes only complete frozen slates; it never runs a model inside this browser request.'}
          </p>
        </aside>

        <aside className="pitcher-ks-readiness pitcher-ks-live-record" aria-label="Live published evaluation summary">
          <span>{evaluation.complete ? 'Final published results' : 'Live published evaluation'}</span>
          <strong>
            {evaluation.graded_starts
              ? `${evaluation.graded_starts} confirmed starts · MAE ${formatNumber(evaluation.mae, 2)} Ks`
              : 'Waiting for official final pitching lines'}
          </strong>
          <p>
            {liveRecord?.graded_starts
              ? `Model-version ledger: ${liveRecord.graded_starts} starts across ${liveRecord.days} graded day${liveRecord.days === 1 ? '' : 's'}, MAE ${formatNumber(liveRecord.mae, 2)}, RMSE ${formatNumber(liveRecord.rmse, 2)}, bias ${formatNumber(liveRecord.bias, 2)}.`
              : 'Live results are kept separate from the historical backtest and exclude pitchers who did not start.'}
          </p>
        </aside>
      </div>

      <section className="pitcher-ks-board" aria-labelledby="pitcher-ks-board-title">
        <div className="pitcher-ks-board-heading">
          <div>
            <span className="hit-history-eyebrow">
              {evaluation.complete ? 'Final projection results' : 'Daily projection board'}
            </span>
            <h3 id="pitcher-ks-board-title">
              {board ? `${board.projection_date} starting pitchers` : 'Today\'s starting pitchers'}
            </h3>
          </div>
          <span className="pitcher-ks-run-state">
            {loading ? 'Loading run' : `${predictions.length} pitchers`}
          </span>
        </div>

        <div className="hit-picks-table-wrap" aria-busy={loading || historyLoading}>
          {historyLoading && <div className="hit-history-loading">Loading date…</div>}
          <table className="audit-signal-table hit-picks-table pitcher-ks-table pitcher-ks-results-table">
            <thead>
              <tr>
                <th>Pitcher</th>
                <th>Matchup</th>
                <th>Projected Ks</th>
                <th>Median</th>
                <th>80% interval</th>
                <th><abbr title="Probability of at least five strikeouts">P(5+)</abbr></th>
                <th><abbr title="Probability of at least six strikeouts">P(6+)</abbr></th>
                <th><abbr title="Projected batters faced">Projected BF</abbr></th>
                <th>Lineup / as of</th>
                <th>Actual Ks</th>
                <th>Error</th>
                <th>Result</th>
              </tr>
            </thead>
            <tbody>
              {predictions.map((prediction) => {
                const result = pitcherResult(prediction)
                return (
                  <tr key={`${prediction.game_pk}-${prediction.pitcher_id}`}>
                    <td>
                      <strong>{prediction.pitcher_name}</strong>
                      <small>{prediction.team} · {prediction.pitcher_throws || '?'}HP</small>
                    </td>
                    <td>
                      vs {prediction.opponent}
                      <small>{prediction.venue || 'Venue pending'}</small>
                    </td>
                    <td><strong>{formatNumber(prediction.projected_ks, 2)}</strong></td>
                    <td>{prediction.median_ks}</td>
                    <td>{prediction.p10_ks}–{prediction.p90_ks}</td>
                    <td>{formatPct(prediction.probability_5_plus)}</td>
                    <td>{formatPct(prediction.probability_6_plus)}</td>
                    <td>{formatNumber(prediction.projected_batters_faced, 1)}</td>
                    <td>
                      {prediction.lineup_source}
                      <small>{formatTimestamp(board?.as_of_timestamp)}</small>
                    </td>
                    <td>
                      {prediction.actual_ks ?? '—'}
                      {prediction.actual_ks !== null && prediction.actual_ks !== undefined && (
                        <small>
                          {prediction.actual_batters_faced ?? '—'} BF · {formatNumber(prediction.actual_innings_pitched, 1)} IP
                        </small>
                      )}
                    </td>
                    <td>{signedError(prediction.error)}</td>
                    <td>
                      <span
                        className={`pitcher-k-result ${result.className}`}
                        title={resultDetail(prediction)}
                        aria-label={`${result.text}: ${resultDetail(prediction)}`}
                      >
                        {result.text}
                      </span>
                    </td>
                  </tr>
                )
              })}
              {!loading && !predictions.length && (
                <tr>
                  <td colSpan="12" className="pitcher-ks-empty-cell">
                    <div className="pitcher-ks-empty">
                      <h3>No pitcher projections available</h3>
                      <p>Run predict_pitcher_ks_today.py after training the model package.</p>
                    </div>
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      <p className="betting-methodology pitcher-ks-result-methodology">
        Final results come from the official MLB game feed and are matched by game ID and pitcher ID.
        Pitchers who did not start are marked DNS and excluded from MAE, RMSE, bias, and probability scores.
      </p>
    </main>
  )
}
