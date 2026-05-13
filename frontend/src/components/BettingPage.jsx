/**
 * BettingPage.jsx - Daily Betting Edge candidates
 * =================================================
 *
 * Renders the top 5-8 hitter candidates for today, ranked by composite
 * score across the signals defined in MLB_Betting_Agent_Instructions.md.
 *
 * Backend contract (GET /betting/candidates):
 *   data.date              "YYYY-MM-DD"
 *   data.generated_at      ISO-8601 UTC, when this set of suggestions was created
 *   data.park_factor_meta  { source, year_range } — provenance of park values
 *   data.candidates        Array<{
 *     rank, player_mlb_id, player_name, player_team,
 *     game_id, opposing_pitcher_mlb_id, opposing_pitcher_name, venue,
 *     composite_score, summary,
 *     signals: {
 *       platoon, pitcher_vulnerability, recent_form, bvp, park_factor:
 *         { value, fired, detail }
 *     }
 *   }>
 *
 * Phase 2 (Bet Audit) will read from the bet_suggestions table this
 * endpoint writes to. See PLAN_BETTING_PAGE.md.
 */

import { useEffect, useState, useCallback } from 'react'
import { API_BASE } from '../config'

// Order matters here — controls which signal chips appear left-to-right
// on each candidate card. Park factor comes last because it's a multiplier
// on the other four, not an additive signal.
const SIGNAL_ORDER = [
  { key: 'platoon',                label: 'Platoon' },
  { key: 'pitcher_vulnerability',  label: 'Pitcher' },
  { key: 'recent_form',            label: 'Form' },
  { key: 'bvp',                    label: 'BvP' },
  { key: 'park_factor',            label: 'Park' },
]


function BettingPage({ onPlayerClick }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [refreshing, setRefreshing] = useState(false)

  const fetchCandidates = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/betting/candidates`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const json = await res.json()
      if (json.code === 200) {
        setData(json.data)
        setError(null)
      } else {
        setError(json.message || 'Failed to fetch candidates')
      }
    } catch (err) {
      setError(`Could not reach backend: ${err.message}`)
    }
  }, [])

  // Initial load
  useEffect(() => {
    setLoading(true)
    fetchCandidates().finally(() => setLoading(false))
  }, [fetchCandidates])

  // Manual refresh — re-runs the generator. Useful if a starter is announced
  // late and the user wants to regenerate the slate. The endpoint is
  // idempotent on suggested_date so this safely overwrites.
  const handleRefresh = async () => {
    setRefreshing(true)
    await fetchCandidates()
    setRefreshing(false)
  }

  if (loading) {
    return (
      <div className="betting-page">
        <div className="betting-loading">Computing today's edge candidates...</div>
      </div>
    )
  }

  return (
    <div className="betting-page">
      <div className="betting-header">
        <div>
          <h2 className="betting-title">Today's Betting Edge</h2>
          {data?.date && (
            <div className="betting-subtitle">
              {data.candidates?.length || 0} candidates for {data.date}
              {data.generated_at && (
                <> · generated {new Date(data.generated_at).toLocaleTimeString()}</>
              )}
            </div>
          )}
        </div>
        <button
          className="matchups-refresh-btn"
          onClick={handleRefresh}
          disabled={refreshing}
        >
          {refreshing ? 'Regenerating...' : 'Regenerate'}
        </button>
      </div>

      {error && (
        <div className="form-message form-message-error">{error}</div>
      )}

      {/* Methodology footer up top so the user understands what they're
          looking at. Phase 2's audit page will be the place to drill in. */}
      <div className="betting-methodology">
        Composite score combines platoon advantage, opposing pitcher
        vulnerability (FIP / WHIP / HR/9 / K-BB%), recent form
        (14-day rolling wOBA vs season xwOBA, gated by rolling K% and
        season Barrel/PA from Baseball Savant), career batter-vs-pitcher
        history (≥10 PA), and park factor. Once ~14 daily Savant
        snapshots have accumulated, the rate-stat ratio auto-upgrades to
        rolling xwOBA vs season xwOBA.
        {' '}
        Park factors:{' '}
        {data?.park_factor_meta?.source === 'baseball_savant' ? (
          <>Baseball Savant {data.park_factor_meta.year_range} rolling.</>
        ) : (
          <>static fallback.</>
        )}
        {' '}
        <em>Each generation fans out ~270 BvP calls and takes 5-10 seconds.</em>
      </div>

      {!error && data && data.candidates?.length === 0 && (
        <div className="betting-empty">
          No candidates available — most likely no MLB games scheduled or no lineups announced yet.
          Check back closer to first pitch.
        </div>
      )}

      <div className="betting-grid">
        {data?.candidates?.map((c) => (
          <div key={c.rank} className="betting-card">
            <div className="betting-card-rank-and-score">
              <span className="betting-rank">#{c.rank}</span>
              <span className="betting-score">{c.composite_score}</span>
            </div>

            <div className="betting-player">
              <span
                className="betting-player-name"
                onClick={() =>
                  onPlayerClick && onPlayerClick({
                    name: c.player_name,
                    mlb_id: c.player_mlb_id,
                    team: c.player_team,
                  })
                }
              >
                {c.player_name}
              </span>
              <span className="betting-team">{c.player_team}</span>
            </div>

            <div className="betting-context">
              <span className="betting-vs">vs</span>{' '}
              <span className="betting-pitcher">{c.opposing_pitcher_name}</span>
              {c.venue && <> · <span className="betting-venue">{c.venue}</span></>}
            </div>

            <div className="betting-signals">
              {SIGNAL_ORDER.map(({ key, label }) => {
                const sig = c.signals?.[key]
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

            <div className="betting-summary">{c.summary}</div>

            {/* Context strip — surfaces the underlying numbers behind
                the signals without requiring the user to hover the chips.
                "—" for missing values lets cold-start (Savant cache empty)
                degrade gracefully. */}
            {c.context_stats && (
              <div className="betting-context-stats">
                <span title="14-day rolling wOBA">
                  rolling wOBA: <strong>{c.context_stats.rolling_woba?.toFixed(3) ?? '—'}</strong>
                </span>
                <span title="Season expected wOBA from Baseball Savant">
                  season xwOBA: <strong>{c.context_stats.season_xwoba?.toFixed(3) ?? '—'}</strong>
                </span>
                <span title="Season Barrels per Plate Appearance (Savant)">
                  Brls/PA: <strong>{c.context_stats.season_barrel_pa_pct?.toFixed(1) ?? '—'}%</strong>
                </span>
                <span title="14-day rolling strikeout rate">
                  K% (14d): <strong>{c.context_stats.rolling_k_pct?.toFixed(1) ?? '—'}%</strong>
                </span>
                <span title="Opposing pitcher's season strikeout-minus-walk rate (K-BB%)">
                  vs. K-BB%: <strong>{c.context_stats.pitcher_k_bb_pct?.toFixed(1) ?? '—'}%</strong>
                </span>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

export default BettingPage
