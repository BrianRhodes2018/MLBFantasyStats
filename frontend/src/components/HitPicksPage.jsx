/**
 * Daily 1+ hit picks with a calendar-backed historical audit view.
 *
 * The model still runs offline. This page reads stored public picks, lets the
 * reader select an available date, and shows the final batting line after the
 * next grading run. Shadow model versions can coexist and become selectable.
 */

import { useEffect, useMemo, useState } from 'react'
import { API_BASE } from '../config'

function formatPct(value, digits = 1) {
  if (value === null || value === undefined) return '-'
  return `${(value * 100).toFixed(digits)}%`
}

function formatRate(value) {
  if (value === null || value === undefined) return '-'
  return value.toFixed(3)
}

function formatStatline(pick) {
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

function resultLabel(pick) {
  if (pick.played === null || pick.played === undefined) {
    return { text: 'Pending', className: 'hit-result-pending' }
  }
  if (!pick.played) return { text: 'DNP', className: 'hit-result-dnp' }
  if (pick.got_hit) return { text: 'Hit', className: 'hit-result-hit' }
  return { text: 'Miss', className: 'hit-result-miss' }
}

async function fetchData(url) {
  const response = await fetch(url)
  if (!response.ok) {
    const detail = (await response.json().catch(() => null))?.detail
    throw new Error(detail || `Request failed (${response.status})`)
  }
  return (await response.json()).data
}

function monthKey(isoDate) {
  return isoDate.slice(0, 7)
}

function moveMonth(key, offset) {
  const [year, month] = key.split('-').map(Number)
  const moved = new Date(year, month - 1 + offset, 1)
  return `${moved.getFullYear()}-${String(moved.getMonth() + 1).padStart(2, '0')}`
}

function calendarCells(key) {
  const [year, month] = key.split('-').map(Number)
  const leading = new Date(year, month - 1, 1).getDay()
  const dayCount = new Date(year, month, 0).getDate()
  const cells = Array.from({ length: leading }, () => null)
  for (let day = 1; day <= dayCount; day += 1) {
    cells.push(`${key}-${String(day).padStart(2, '0')}`)
  }
  while (cells.length % 7) cells.push(null)
  return cells
}

function monthLabel(key) {
  const [year, month] = key.split('-').map(Number)
  return new Intl.DateTimeFormat('en-US', {
    month: 'long',
    year: 'numeric',
  }).format(new Date(year, month - 1, 1))
}

function HitPicksCalendar({
  dates,
  selectedDate,
  visibleMonth,
  onMonthChange,
  onSelectDate,
  latestDate,
  loading,
}) {
  const metadata = useMemo(
    () => new Map(dates.map((item) => [item.date, item])),
    [dates],
  )
  const cells = useMemo(() => calendarCells(visibleMonth), [visibleMonth])

  return (
    <section className="hit-history-panel" aria-label="Hit picks history calendar">
      <div className="hit-history-heading">
        <div>
          <span className="hit-history-eyebrow">Pick history</span>
          <h3>Select a game date</h3>
        </div>
        <button
          type="button"
          className="hit-history-latest"
          onClick={() => latestDate && onSelectDate(latestDate)}
          disabled={!latestDate || latestDate === selectedDate || loading}
        >
          Latest
        </button>
      </div>

      <div className="hit-calendar-nav">
        <button
          type="button"
          aria-label="Previous month"
          onClick={() => onMonthChange(moveMonth(visibleMonth, -1))}
        >
          ‹
        </button>
        <strong>{monthLabel(visibleMonth)}</strong>
        <button
          type="button"
          aria-label="Next month"
          onClick={() => onMonthChange(moveMonth(visibleMonth, 1))}
        >
          ›
        </button>
      </div>

      <div className="hit-calendar-weekdays" aria-hidden="true">
        {['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'].map((day) => (
          <span key={day}>{day}</span>
        ))}
      </div>
      <div className="hit-calendar-grid">
        {cells.map((isoDate, index) => {
          if (!isoDate) {
            return <span className="hit-calendar-empty" key={`empty-${index}`} />
          }
          const day = metadata.get(isoDate)
          const isSelected = isoDate === selectedDate
          const status = day?.grading_status
          return (
            <button
              type="button"
              key={isoDate}
              aria-label={
                day
                  ? `Show picks for ${isoDate}, ${status}`
                  : `${isoDate}, no saved picks`
              }
              className={[
                'hit-calendar-day',
                day ? 'available' : '',
                isSelected ? 'selected' : '',
                status ? `status-${status}` : '',
              ].filter(Boolean).join(' ')}
              disabled={!day || loading}
              onClick={() => onSelectDate(isoDate)}
            >
              <span>{Number(isoDate.slice(-2))}</span>
              {day && <i aria-hidden="true" />}
            </button>
          )
        })}
      </div>
      <div className="hit-calendar-legend">
        <span><i className="graded" /> Graded</span>
        <span><i className="pending" /> Pending</span>
      </div>
    </section>
  )
}

export default function HitPicksPage() {
  const [picks, setPicks] = useState(null)
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

    async function load() {
      try {
        const [latest, history, trackRecord] = await Promise.all([
          fetchData(`${API_BASE}/hit-picks/latest?top=15`),
          fetchData(`${API_BASE}/hit-picks/dates?limit=365`).catch(() => ({
            dates: [],
            latest_date: null,
          })),
          fetchData(`${API_BASE}/hit-picks/ledger`).catch(() => null),
        ])
        if (cancelled) return
        setPicks(latest)
        setSelectedDate(latest.date)
        setVisibleMonth(monthKey(latest.date))
        setHistoryDates(history.dates?.length ? history.dates : [{
          date: latest.date,
          grading_status: latest.grading_status || 'pending',
        }])
        setLedger(trackRecord)
      } catch (err) {
        if (!cancelled) setError(err.message)
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    load()
    return () => { cancelled = true }
  }, [])

  async function loadHistoricalDate(isoDate, modelVersion = null) {
    setHistoryLoading(true)
    setHistoryError(null)
    try {
      const modelQuery = modelVersion
        ? `&model_version=${encodeURIComponent(modelVersion)}`
        : ''
      const historical = await fetchData(
        `${API_BASE}/hit-picks/${isoDate}?top=15${modelQuery}`,
      )
      setPicks(historical)
      setSelectedDate(isoDate)
      setVisibleMonth(monthKey(isoDate))
    } catch (err) {
      setHistoryError(err.message)
    } finally {
      setHistoryLoading(false)
    }
  }

  if (loading) return <div className="betting-loading">Loading hit picks…</div>
  if (error) return <div className="betting-empty">Could not load hit picks: {error}</div>
  if (!picks || !picks.picks?.length) {
    return <div className="betting-empty">No picks available yet.</div>
  }

  const versionRecord = ledger?.summary?.[picks.model_version]
  const latestDate = historyDates[0]?.date || picks.date
  const completed = picks.picks.filter((pick) => pick.played === 1)
  const dayHits = completed.filter((pick) => pick.got_hit === 1).length
  const models = picks.available_models || []

  return (
    <div className="betting-page">
      <HitPicksCalendar
        dates={historyDates}
        selectedDate={selectedDate}
        visibleMonth={visibleMonth}
        onMonthChange={setVisibleMonth}
        onSelectDate={loadHistoricalDate}
        latestDate={latestDate}
        loading={historyLoading}
      />

      <div className="betting-header hit-picks-header">
        <div>
          <span className="hit-history-eyebrow">
            {picks.grading_status === 'graded' ? 'Final results' : 'Current board'}
          </span>
          <h2>Daily 1+ Hit Model Picks — {picks.date}</h2>
        </div>
        {models.length > 1 && (
          <label className="hit-model-selector">
            Model
            <select
              value={picks.model_version}
              disabled={historyLoading}
              onChange={(event) => loadHistoricalDate(picks.date, event.target.value)}
            >
              {models.map((model) => (
                <option key={model.model_version} value={model.model_version}>
                  {model.model_version}{model.is_public ? ' (public)' : ' (shadow)'}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>

      {historyError && (
        <div className="hit-history-error" role="alert">
          Could not load that date: {historyError}
        </div>
      )}

      <p className="betting-methodology">
        Model <strong>{picks.model_version}</strong>, trained on{' '}
        {picks.trained_on_rows?.toLocaleString()} batter-games (2023–present).
        Lineups are projected from recent boxscores until officials post.
        {picks.grading_status === 'graded' && completed.length > 0 && (
          <>
            {' '}This board finished <strong>{dayHits}/{completed.length}</strong>{' '}
            among the displayed players who appeared.
          </>
        )}
        {versionRecord?.top10?.played ? (
          <>
            {' '}Live track record for this model version:{' '}
            <strong>
              {versionRecord.top10.hits}/{versionRecord.top10.played} (
              {formatPct(versionRecord.top10.hit_rate, 0)})
            </strong>{' '}
            on top-10 picks over {versionRecord.days} graded day
            {versionRecord.days === 1 ? '' : 's'}.
          </>
        ) : (
          ' No graded results for this model version yet.'
        )}
      </p>

      <div className="hit-picks-table-wrap" aria-busy={historyLoading}>
        {historyLoading && <div className="hit-history-loading">Loading date…</div>}
        <table className="audit-signal-table hit-picks-table">
          <thead>
            <tr>
              <th>#</th>
              <th>Player</th>
              <th>Team</th>
              <th>Slot</th>
              <th>Hit Prob</th>
              <th>L10 H/PA</th>
              <th>Opposing Pitcher</th>
              <th>Platoon</th>
              <th>Result</th>
              <th>Actual line</th>
            </tr>
          </thead>
          <tbody>
            {picks.picks.map((pick, idx) => {
              const result = resultLabel(pick)
              return (
                <tr key={`${picks.date}-${picks.model_version}-${pick.player_id ?? idx}`}>
                  <td>{pick.rank ?? idx + 1}</td>
                  <td>
                    {pick.player_name}
                    {pick.bats ? ` (${pick.bats})` : ''}
                  </td>
                  <td>{pick.team}</td>
                  <td>{pick.batting_order}</td>
                  <td><strong>{formatPct(pick.hit_probability)}</strong></td>
                  <td>{formatRate(pick.last10_hit_per_pa)}</td>
                  <td>
                    {pick.pitcher_name}
                    {pick.pitcher_throws ? ` (${pick.pitcher_throws}HP)` : ''}
                  </td>
                  <td>
                    {pick.platoon_advantage === 1
                      ? '✓'
                      : pick.platoon_advantage === 0 ? '—' : '?'}
                  </td>
                  <td><span className={`hit-result ${result.className}`}>{result.text}</span></td>
                  <td><span className="audit-statline">{formatStatline(pick)}</span></td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      <p className="betting-methodology" style={{ marginTop: '16px' }}>
        Probabilities are walk-forward validated (see backend/train_hit_model.py):
        the model is only ever evaluated on days it has never seen. A pick is
        graded a win when the player records at least one hit; players who do
        not appear are marked DNP and excluded from the model hit-rate denominator.
      </p>
    </div>
  )
}
