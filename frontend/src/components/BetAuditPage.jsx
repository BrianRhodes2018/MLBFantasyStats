/**
 * BetAuditPage.jsx - "Are our betting picks any good?"
 * ======================================================
 *
 * Reads from GET /betting/audit and renders three layers:
 *   1. Aggregate band — overall hit rates and freshness across the date window
 *   2. Per-signal table — same hit rates sliced by which signal fired (the
 *      feedback loop for Phase 3 weight tuning)
 *   3. Suggestion log — one row per (date, candidate) with actual stat line,
 *      hit/miss flags, fired signals
 *
 * Outcome definitions (tracked because there's no real sportsbook prop here):
 *   - hit_2tb : actual_total_bases >= 2 (multi-hit or extra-base game)
 *   - hit_xbh : at least one extra-base hit
 *
 * Filters:
 *   - Date range (from / to)
 *   - Signal — only show suggestions where this specific signal fired
 *   - Min score
 *
 * Phase 3 will use this view's per-signal hit rates to decide which
 * weights to bump and which to drop.
 */

import { useEffect, useState, useCallback, useMemo } from 'react'
import { API_BASE } from '../config'

// Same canonical order as the betting page so the audit table reads
// consistently.
const SIGNAL_NAMES = [
  { key: 'platoon',                label: 'Platoon' },
  { key: 'pitcher_vulnerability',  label: 'Pitcher' },
  { key: 'recent_form',            label: 'Form' },
  { key: 'bvp',                    label: 'BvP' },
  { key: 'park_factor',            label: 'Park' },
]

// Helper: format a percentage as "57.0%" or "—" when the sample is empty.
// We deliberately render "—" for empty samples instead of "0%" so the user
// can distinguish "we have data and it's 0%" from "we have no data yet".
function fmtPct(pct) {
  return pct === null || pct === undefined ? '—' : `${pct.toFixed(1)}%`
}

// Helper: human-readable summary of how the player actually performed.
// "3-for-4, 1 HR, 4 RBI" is what you'd glance at in a box score — friendlier
// than the dense "AB-H-2B-3B-HR-RBI" format.
function fmtActualLine(s) {
  const parts = []
  parts.push(`${s.actual_hits ?? 0}-for-${s.actual_at_bats ?? 0}`)
  if ((s.actual_doubles ?? 0) > 0) parts.push(`${s.actual_doubles} 2B`)
  if ((s.actual_triples ?? 0) > 0) parts.push(`${s.actual_triples} 3B`)
  if ((s.actual_home_runs ?? 0) > 0) parts.push(`${s.actual_home_runs} HR`)
  if ((s.actual_rbi ?? 0) > 0) parts.push(`${s.actual_rbi} RBI`)
  if ((s.actual_walks ?? 0) > 0) parts.push(`${s.actual_walks} BB`)
  if ((s.actual_strikeouts ?? 0) > 0) parts.push(`${s.actual_strikeouts} K`)
  return `${parts.join(', ')} (${s.actual_total_bases ?? 0} TB)`
}

// Default the filter window to "last 30 days inclusive" — matches what the
// backend uses when from/to are omitted.
function defaultFrom() {
  const d = new Date()
  d.setDate(d.getDate() - 30)
  return d.toISOString().slice(0, 10)
}
function defaultTo() {
  return new Date().toISOString().slice(0, 10)
}

// Render "2026-05-09" as "Saturday, May 9, 2026" — matches the kind of
// header the live Betting Edge page would show that day.
function formatDateLong(yyyymmdd) {
  // Append T12:00:00 to avoid UTC shifting the date back a day in some zones
  const d = new Date(`${yyyymmdd}T12:00:00`)
  return d.toLocaleDateString(undefined, {
    weekday: 'long',
    year: 'numeric',
    month: 'long',
    day: 'numeric',
  })
}


function BetAuditPage({ onPlayerClick }) {
  // Filter state — empty string means "let the backend default it"
  const [fromDate, setFromDate] = useState(defaultFrom())
  const [toDate, setToDate] = useState(defaultTo())
  const [signalFilter, setSignalFilter] = useState('')
  const [minScore, setMinScore] = useState('')

  // Response state
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  // Group suggestions by date so we can render each date as its own section,
  // matching how the original Betting Edge page would have looked. Server
  // already orders newest-first within each date, so we preserve that.
  const groupedByDate = useMemo(() => {
    const map = new Map()
    for (const s of data?.suggestions || []) {
      if (!map.has(s.suggested_date)) map.set(s.suggested_date, [])
      map.get(s.suggested_date).push(s)
    }
    return Array.from(map.entries())
  }, [data?.suggestions])

  const fetchAudit = useCallback(async () => {
    try {
      const params = new URLSearchParams()
      if (fromDate) params.set('from', fromDate)
      if (toDate) params.set('to', toDate)
      if (signalFilter) params.set('signal', signalFilter)
      if (minScore) params.set('min_score', minScore)
      const res = await fetch(`${API_BASE}/betting/audit?${params.toString()}`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const json = await res.json()
      if (json.code === 200) {
        setData(json.data)
        setError(null)
      } else {
        setError(json.message || 'Failed to fetch audit data')
      }
    } catch (err) {
      setError(`Could not reach backend: ${err.message}`)
    }
  }, [fromDate, toDate, signalFilter, minScore])

  useEffect(() => {
    setLoading(true)
    fetchAudit().finally(() => setLoading(false))
  }, [fetchAudit])

  if (loading) {
    return (
      <div className="audit-page">
        <div className="betting-loading">Loading audit data...</div>
      </div>
    )
  }

  return (
    <div className="audit-page">
      <div className="betting-header">
        <div>
          <h2 className="betting-title">Bet Audit</h2>
          <div className="betting-subtitle">
            How our picks have actually performed
          </div>
        </div>
      </div>

      {error && (
        <div className="form-message form-message-error">{error}</div>
      )}

      {/* Filter bar — date range, signal filter, min-score filter.
          Each input directly drives the fetch via useEffect, so submitting
          isn't needed. Auto-debouncing is unnecessary at the audit page's
          read-only volume. */}
      <div className="audit-filters">
        <label>
          From{' '}
          <input
            type="date"
            value={fromDate}
            onChange={(e) => setFromDate(e.target.value)}
          />
        </label>
        <label>
          To{' '}
          <input
            type="date"
            value={toDate}
            onChange={(e) => setToDate(e.target.value)}
          />
        </label>
        <label>
          Signal{' '}
          <select
            value={signalFilter}
            onChange={(e) => setSignalFilter(e.target.value)}
          >
            <option value="">any</option>
            {SIGNAL_NAMES.map(({ key, label }) => (
              <option key={key} value={key}>{label}</option>
            ))}
          </select>
        </label>
        <label>
          Min score{' '}
          <input
            type="number"
            value={minScore}
            onChange={(e) => setMinScore(e.target.value)}
            placeholder="any"
            min="0"
            max="200"
            step="1"
            style={{ width: '70px' }}
          />
        </label>
      </div>

      {/* Aggregate summary band.
          Big numbers — the user wants to know "are we any good?" at a glance. */}
      {data?.aggregates && (
        <div className="audit-aggregates">
          <div className="audit-stat">
            <div className="audit-stat-label">Total suggestions</div>
            <div className="audit-stat-value">{data.aggregates.total_suggestions}</div>
            <div className="audit-stat-sub">
              {data.aggregates.backfilled_count} have actuals ({data.aggregates.freshness_pct}% fresh)
            </div>
          </div>
          <div className="audit-stat">
            <div className="audit-stat-label">Hit rate (≥ 2 TB)</div>
            <div className="audit-stat-value">{fmtPct(data.aggregates.hit_rate_2tb)}</div>
            <div className="audit-stat-sub">2+ total bases in the suggested game</div>
          </div>
          <div className="audit-stat">
            <div className="audit-stat-label">Hit rate (≥ 1 XBH)</div>
            <div className="audit-stat-value">{fmtPct(data.aggregates.hit_rate_xbh)}</div>
            <div className="audit-stat-sub">at least one extra-base hit</div>
          </div>
        </div>
      )}

      {/* Per-signal hit rates — the feedback loop for tuning weights.
          A signal with high count + high hit rate is pulling its weight;
          a signal with low hit rate is a candidate for de-weighting. */}
      {data?.per_signal && (
        <div className="audit-per-signal">
          <h3 className="audit-section-title">Per-signal performance</h3>
          <table className="audit-signal-table">
            <thead>
              <tr>
                <th>Signal</th>
                <th>Suggestions where fired</th>
                <th>Hit rate (≥ 2 TB)</th>
                <th>Hit rate (≥ 1 XBH)</th>
              </tr>
            </thead>
            <tbody>
              {SIGNAL_NAMES.map(({ key, label }) => {
                const s = data.per_signal[key] || {}
                return (
                  <tr key={key}>
                    <td>{label}</td>
                    <td>{s.count ?? 0}</td>
                    <td>{fmtPct(s.hit_rate_2tb)}</td>
                    <td>{fmtPct(s.hit_rate_xbh)}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
          <div className="audit-meta-note">
            High count + high hit rate ⇒ pulling its weight. Low hit rate
            despite frequent firing ⇒ candidate to down-weight in the
            scoring function.
          </div>
        </div>
      )}

      {/* Suggestion history — grouped by date so each section reads like
          the original Betting Edge page for that date, with the actual
          outcome appended to each card. The full reasoning that was shown
          when the suggestion was generated (signal details, fired chips,
          summary line) renders identically here, so you can see *why* a
          pick was made and how it performed. */}
      <h3 className="audit-section-title">Suggestion history</h3>
      {(!data?.suggestions || data.suggestions.length === 0) ? (
        <div className="betting-empty">
          No suggestions in this date range.
          {' '}
          {(!data?.aggregates?.total_suggestions) && (
            <em>The audit fills in over time — make sure /betting/candidates is being generated daily.</em>
          )}
        </div>
      ) : (
        groupedByDate.map(([date, items]) => (
          <div key={date} className="audit-date-group">
            <div className="audit-date-header">
              <span className="audit-date-label">{formatDateLong(date)}</span>
              <span className="audit-date-count">
                {items.length} candidate{items.length === 1 ? '' : 's'}
                {' · '}
                {(() => {
                  const backfilled = items.filter(s => !s.outcome_pending)
                  const hits2tb = backfilled.filter(s => s.hit_2tb).length
                  if (backfilled.length === 0) return 'awaiting backfill'
                  return `${hits2tb}/${backfilled.length} hit (≥2TB)`
                })()}
              </span>
            </div>
            <div className="betting-grid">
              {items.map((s) => (
                <div key={s.id} className="betting-card">
                  {/* Same card layout as the live Betting Edge page — rank,
                      score, player, context, signals, summary. */}
                  <div className="betting-card-rank-and-score">
                    <span className="betting-rank">#{s.rank}</span>
                    <span className="betting-score">{s.composite_score}</span>
                  </div>

                  <div className="betting-player">
                    <span
                      className="betting-player-name"
                      onClick={() =>
                        onPlayerClick && onPlayerClick({
                          name: s.player_name,
                          mlb_id: s.player_mlb_id,
                          team: s.player_team,
                        })
                      }
                    >
                      {s.player_name}
                    </span>
                    <span className="betting-team">{s.player_team}</span>
                  </div>

                  <div className="betting-context">
                    <span className="betting-vs">vs</span>{' '}
                    <span className="betting-pitcher">{s.opposing_pitcher_name}</span>
                    {s.venue && <> · <span className="betting-venue">{s.venue}</span></>}
                  </div>

                  <div className="betting-signals">
                    {SIGNAL_NAMES.map(({ key, label }) => {
                      const sig = s.signals?.[key]
                      if (!sig) return null
                      return (
                        <span
                          key={key}
                          className={`betting-signal-chip ${sig.fired ? 'fired' : 'dim'}`}
                          title={sig.detail}
                        >
                          {sig.fired ? '✓' : '·'} {label}
                        </span>
                      )
                    })}
                  </div>

                  <div className="betting-summary">{s.summary}</div>

                  {/* Actual outcome — separated by a divider so you see at a
                      glance "this is the prediction; below is what happened". */}
                  <div className="audit-card-outcome">
                    {s.outcome_pending ? (
                      <span className="audit-outcome-pending">awaiting backfill...</span>
                    ) : s.actual_skip_reason ? (
                      <span className="audit-outcome-skip">{s.actual_skip_reason}</span>
                    ) : (
                      <>
                        <span className="audit-outcome-line">{fmtActualLine(s)}</span>
                        <span className={`audit-outcome-flag ${s.hit_2tb ? 'hit' : (s.hit_xbh ? 'partial' : 'miss')}`}>
                          {s.hit_2tb ? '✓ ≥2TB' : (s.hit_xbh ? '✓ XBH' : '✗')}
                        </span>
                      </>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>
        ))
      )}
    </div>
  )
}

export default BetAuditPage
