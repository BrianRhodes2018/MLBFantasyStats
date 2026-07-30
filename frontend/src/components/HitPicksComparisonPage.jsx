import { useEffect, useState } from 'react'

import { API_BASE } from '../config'
import {
  fetchData,
  formatPct,
  formatStatline,
  monthKey,
  resultLabel,
} from '../utils/hitPicksUi'
import {
  HitPicksCalendar,
} from './HitPicksPage'

function signed(value, digits = 3) {
  if (value === null || value === undefined) return '—'
  return `${value >= 0 ? '+' : ''}${value.toFixed(digits)}`
}

function rankMovement(row) {
  if (row.entered_top) return 'Entered top 15'
  if (row.left_top) return 'Left top 15'
  if (row.rank_movement === null || row.rank_movement === undefined) return '—'
  if (row.rank_movement === 0) return 'No change'
  return `${row.rank_movement > 0 ? '▲' : '▼'} ${Math.abs(row.rank_movement)}`
}

export default function HitPicksComparisonPage() {
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
      fetchData(`${API_BASE}/hit-picks/compare/latest?top=15`),
      fetchData(`${API_BASE}/hit-picks/boards/primary/dates?limit=365`).catch(() => ({
        dates: [],
      })),
    ])
      .then(([latest, history]) => {
        if (cancelled) return
        setComparison(latest)
        setSelectedDate(latest.date)
        setVisibleMonth(monthKey(latest.date))
        setDates(history.dates || [])
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
      const data = await fetchData(
        `${API_BASE}/hit-picks/compare/${isoDate}?top=15`,
      )
      setComparison(data)
      setSelectedDate(isoDate)
      setVisibleMonth(monthKey(isoDate))
    } catch (err) {
      setError(err.message)
    } finally {
      setHistoryLoading(false)
    }
  }

  if (loading) return <div className="betting-loading">Loading comparison…</div>
  if (error && !comparison) {
    return <div className="betting-empty">Could not load comparison: {error}</div>
  }

  const latestDate = dates[0]?.date || comparison?.date
  return (
    <div className="betting-page">
      <HitPicksCalendar
        dates={dates}
        selectedDate={selectedDate}
        visibleMonth={visibleMonth}
        onMonthChange={setVisibleMonth}
        onSelectDate={loadDate}
        latestDate={latestDate}
        loading={historyLoading}
      />

      <div className="betting-header hit-picks-header">
        <div>
          <span className="hit-history-eyebrow">Strict paired evaluation</span>
          <h2>V2 vs V3 Hit Picks — {comparison?.date}</h2>
        </div>
        <span className="hit-experimental-badge">V3 experimental</span>
      </div>

      {error && <div className="hit-history-error" role="alert">{error}</div>}

      {!comparison?.comparable ? (
        <div className="hit-v3-empty">
          <h3>No paired V3 run for this snapshot</h3>
          <p>{comparison?.reason || 'V3 has not produced a comparable board yet.'}</p>
          <small>
            A comparison appears only when both models used the same players,
            lineup snapshot, prediction window, and as-of time.
          </small>
        </div>
      ) : (
        <>
          <p className="betting-methodology">
            Window <strong>{comparison.prediction_window}</strong>. Shared coverage:{' '}
            <strong>
              {comparison.coverage.shared_candidates}/
              {comparison.coverage.primary_candidates}
            </strong>{' '}
            ({formatPct(comparison.coverage.challenger_fraction)}). V2 is a
            calibrated probability; V3 remains an experimental model score.
          </p>
          <div className="hit-picks-table-wrap" aria-busy={historyLoading}>
            <table className="audit-signal-table hit-picks-table hit-compare-table">
              <thead>
                <tr>
                  <th>Player</th>
                  <th>V2 rank</th>
                  <th>V2 probability</th>
                  <th>V3 rank</th>
                  <th>V3 score</th>
                  <th>Score Δ</th>
                  <th>Rank move</th>
                  <th>Lineup / pitcher</th>
                  <th>Result</th>
                  <th>Actual line</th>
                </tr>
              </thead>
              <tbody>
                {comparison.rows.map((row) => {
                  const result = resultLabel(row)
                  return (
                    <tr key={`${row.game_pk}-${row.player_id}`}>
                      <td>{row.player_name}<small>{row.team}</small></td>
                      <td>{row.primary_rank ?? '—'}</td>
                      <td>{row.primary_score == null ? '—' : formatPct(row.primary_score)}</td>
                      <td>{row.challenger_rank ?? '—'}</td>
                      <td>
                        {row.challenger_score == null
                          ? '—'
                          : row.challenger_score.toFixed(3)}
                      </td>
                      <td>{signed(row.score_delta)}</td>
                      <td>{rankMovement(row)}</td>
                      <td>
                        {row.lineup_source || 'unknown'}
                        <small>{row.pitcher_name || 'Pitcher unavailable'}</small>
                      </td>
                      <td>
                        <span className={`hit-result ${result.className}`}>
                          {result.text}
                        </span>
                      </td>
                      <td><span className="audit-statline">{formatStatline(row)}</span></td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  )
}
