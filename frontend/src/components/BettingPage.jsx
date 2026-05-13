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

import { useEffect, useMemo, useState, useCallback } from 'react'
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


// MLB game statuses that indicate the game has started (and so we can't
// realistically bet on its candidates anymore). MLB Stats API uses a
// handful of string values here; we match defensively rather than trying
// to enumerate every state ("In Progress", "Live", "Final", "Game Over",
// "Completed Early", "Suspended", etc. all imply the game has begun).
function gameHasStarted(status) {
  if (!status) return false
  const s = String(status).toLowerCase()
  return (
    s.includes('progress') ||
    s.includes('live') ||
    s.includes('final') ||
    s.includes('over') ||
    s.includes('completed') ||
    s.includes('suspended') ||
    s.includes('delayed') ||
    s.includes('postponed')
  )
}

// Format an ISO datetime to a friendly local-time string for game headers.
// "2026-05-13T17:05:00Z" -> "1:05 PM" in user's local timezone.
function formatGameTime(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })
}


function BettingPage({ onPlayerClick }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [refreshing, setRefreshing] = useState(false)
  // User toggle: when true, started/finished games appear in the list
  // (greyed out). Default false — once a game starts you can't bet on it
  // anymore, so hide it to reduce visual noise.
  const [showStarted, setShowStarted] = useState(false)

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

  // Group candidates by game_id and sort the groups by game_time. Each
  // game becomes a section in the UI under a "1:05 PM ET vs Yankees @
  // Yankee Stadium" header. Within a section, candidates remain ordered
  // by composite_score (the backend already sorts them this way).
  //
  // Memoized because the work is O(N log N) and useEffect would otherwise
  // recompute it on every state change.
  const gameGroups = useMemo(() => {
    if (!data?.candidates?.length) return []
    const map = new Map()
    for (const c of data.candidates) {
      const key = c.game_id ?? c.opposing_pitcher_name ?? c.venue ?? 'unknown'
      if (!map.has(key)) {
        map.set(key, {
          game_id: c.game_id,
          game_time: c.game_time,
          game_status: c.game_status,
          venue: c.venue,
          opposing_pitcher_name: c.opposing_pitcher_name,
          candidates: [],
        })
      }
      map.get(key).candidates.push(c)
    }
    // Sort groups by game_time ascending so morning games come first.
    // Groups missing game_time fall to the bottom.
    return Array.from(map.values()).sort((a, b) => {
      if (!a.game_time && !b.game_time) return 0
      if (!a.game_time) return 1
      if (!b.game_time) return -1
      return a.game_time.localeCompare(b.game_time)
    })
  }, [data?.candidates])

  // Filter games that have started unless the user explicitly opted in.
  const visibleGroups = useMemo(() => {
    if (showStarted) return gameGroups
    return gameGroups.filter(g => !gameHasStarted(g.game_status))
  }, [gameGroups, showStarted])

  // Count of started groups, for the toggle's label text.
  const hiddenStartedCount = gameGroups.length - visibleGroups.length

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
          No candidates cleared the quality bar
          {data.thresholds && (
            <> (composite ≥ {data.thresholds.min_composite_score}, ≥ {data.thresholds.min_fired_signals} signals firing)</>
          )}
          {' '}— either no MLB games are scheduled, no lineups have posted yet,
          or today's matchups simply don't produce strong picks. Check back
          closer to later games' first pitches.
        </div>
      )}

      {/* "Show started games" toggle. Only render when there's something
          to toggle (i.e. some games have already started). */}
      {hiddenStartedCount > 0 && (
        <div className="betting-toggle-row">
          <label>
            <input
              type="checkbox"
              checked={showStarted}
              onChange={(e) => setShowStarted(e.target.checked)}
            />
            {' '}Show {hiddenStartedCount} game{hiddenStartedCount === 1 ? '' : 's'} already in progress
          </label>
        </div>
      )}

      {/* Game-grouped sections. Each game's candidates are rendered as a
          subsection under a header showing start time + matchup, oldest
          game first. As the day progresses and later-game lineups post,
          more sections appear on the next page refresh. */}
      {visibleGroups.map((group) => {
        const started = gameHasStarted(group.game_status)
        return (
          <div
            key={group.game_id ?? group.opposing_pitcher_name}
            className={`betting-game-group${started ? ' started' : ''}`}
          >
            <div className="betting-game-header">
              <span className="betting-game-time">{formatGameTime(group.game_time)}</span>
              <span className="betting-game-separator">·</span>
              <span className="betting-game-matchup">
                {group.candidates[0]?.player_team || 'Lineup'} vs {group.opposing_pitcher_name}
              </span>
              {group.venue && (
                <span className="betting-game-venue"> @ {group.venue}</span>
              )}
              {group.game_status && (
                <span className="betting-game-status">[{group.game_status}]</span>
              )}
              <span className="betting-game-count">
                {group.candidates.length} pick{group.candidates.length === 1 ? '' : 's'}
              </span>
            </div>

            <div className="betting-grid">
              {group.candidates.map((c) => (
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

                  {/* Context strip — underlying numbers behind the signals
                      without requiring chip-hover. "—" for missing values. */}
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
      })}
    </div>
  )
}

export default BettingPage
