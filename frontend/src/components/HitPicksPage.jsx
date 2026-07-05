/**
 * HitPicksPage.jsx - Daily 1+ Hit Model Picks
 * ============================================
 *
 * Displays the ranked pick list produced by the trained hit-prediction
 * model (backend/predict_hits_today.py). The backend endpoint just
 * serves the most recent saved pick file — no model runs per request —
 * so this page is fast and works even while a new day's list is still
 * being generated.
 *
 * Also shows the model's live track record from /hit-picks/ledger
 * (maintained by backend/grade_hit_picks.py), so the displayed hit
 * rates are REAL graded results, not backtest promises.
 */

import { useState, useEffect } from 'react'
import { API_BASE } from '../config'

function formatPct(value, digits = 1) {
  if (value === null || value === undefined) return '-'
  return `${(value * 100).toFixed(digits)}%`
}

function formatRate(value) {
  if (value === null || value === undefined) return '-'
  return value.toFixed(3)
}

export default function HitPicksPage() {
  const [picks, setPicks] = useState(null)      // { date, model_version, picks: [...] }
  const [ledger, setLedger] = useState(null)    // { summary: {version: {...}}, days_graded }
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false

    async function load() {
      try {
        const picksRes = await fetch(`${API_BASE}/hit-picks/latest?top=15`)
        if (!picksRes.ok) {
          const detail = (await picksRes.json().catch(() => null))?.detail
          throw new Error(detail || `Request failed (${picksRes.status})`)
        }
        const picksJson = await picksRes.json()
        if (!cancelled) setPicks(picksJson.data)

        // The ledger is optional — a 404 just means nothing graded yet.
        const ledgerRes = await fetch(`${API_BASE}/hit-picks/ledger`)
        if (ledgerRes.ok) {
          const ledgerJson = await ledgerRes.json()
          if (!cancelled) setLedger(ledgerJson.data)
        }
      } catch (err) {
        if (!cancelled) setError(err.message)
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    load()
    return () => { cancelled = true }
  }, [])

  if (loading) return <div className="betting-loading">Loading hit picks…</div>
  if (error) return <div className="betting-empty">Could not load hit picks: {error}</div>
  if (!picks || !picks.picks?.length) return <div className="betting-empty">No picks available yet.</div>

  const versionRecord = ledger?.summary?.[picks.model_version]

  return (
    <div className="betting-page">
      <div className="betting-header">
        <h2>Daily 1+ Hit Model Picks — {picks.date}</h2>
        <p className="betting-methodology">
          Model <strong>{picks.model_version}</strong>, trained on{' '}
          {picks.trained_on_rows?.toLocaleString()} batter-games (2023–present).
          Lineups are projected from recent boxscores until officials post.
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
      </div>

      <table className="audit-signal-table">
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
          </tr>
        </thead>
        <tbody>
          {picks.picks.map((pick, idx) => (
            <tr key={pick.player_id ?? idx}>
              <td>{idx + 1}</td>
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
              <td>{pick.platoon_advantage === 1 ? '✓' : pick.platoon_advantage === 0 ? '—' : '?'}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <p className="betting-methodology" style={{ marginTop: '16px' }}>
        Probabilities are walk-forward validated (see backend/train_hit_model.py):
        the model is only ever evaluated on days it has never seen. A pick is
        graded a win when the player records at least one hit; players who end
        up not starting are excluded from grading.
      </p>
    </div>
  )
}
