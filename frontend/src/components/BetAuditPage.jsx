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

import { useEffect, useState, useCallback } from 'react'
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

// Helper: render the actual stat line as "AB-H-2B-3B-HR-RBI" for compact display
function fmtStatLine(s) {
  if (s.outcome_pending) return 'pending'
  if (s.actual_skip_reason) return s.actual_skip_reason
  return `${s.actual_at_bats ?? '-'}-${s.actual_hits ?? '-'}-${s.actual_doubles ?? '-'}-${s.actual_triples ?? '-'}-${s.actual_home_runs ?? '-'}-${s.actual_rbi ?? '-'}`
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

      {/* Suggestion log — the raw history.
          Sorted newest-first by the backend. Click a player name to open
          the existing PlayerModal. Stat-line column shows AB-H-2B-3B-HR-RBI
          which is compact and box-score-familiar. */}
      <h3 className="audit-section-title">Suggestion log</h3>
      {(!data?.suggestions || data.suggestions.length === 0) ? (
        <div className="betting-empty">
          No suggestions in this date range.
          {' '}
          {(!data?.aggregates?.total_suggestions) && (
            <em>The audit fills in over time — make sure /betting/candidates is being generated daily.</em>
          )}
        </div>
      ) : (
        <table className="audit-log-table">
          <thead>
            <tr>
              <th>Date</th>
              <th>#</th>
              <th>Player</th>
              <th>Pitcher</th>
              <th>Score</th>
              <th>Fired</th>
              <th>Actual (AB-H-2B-3B-HR-RBI)</th>
              <th>Hit?</th>
            </tr>
          </thead>
          <tbody>
            {data.suggestions.map((s) => {
              const firedSignals = SIGNAL_NAMES
                .filter(({ key }) => s.signals?.[key]?.fired)
                .map(({ label }) => label)
                .join(', ') || '—'
              const hitFlag = s.outcome_pending
                ? <span className="audit-hit-pending">pending</span>
                : (s.hit_2tb
                    ? <span className="audit-hit-yes">✓ ≥2TB</span>
                    : (s.hit_xbh
                        ? <span className="audit-hit-yes">✓ XBH</span>
                        : <span className="audit-hit-no">✗</span>
                      )
                  )
              return (
                <tr key={s.id}>
                  <td>{s.suggested_date}</td>
                  <td>{s.rank}</td>
                  <td>
                    <span
                      className="audit-player-name"
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
                  </td>
                  <td>{s.opposing_pitcher_name}</td>
                  <td>{s.composite_score}</td>
                  <td>{firedSignals}</td>
                  <td className="audit-statline">{fmtStatLine(s)}</td>
                  <td>{hitFlag}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}
    </div>
  )
}

export default BetAuditPage
