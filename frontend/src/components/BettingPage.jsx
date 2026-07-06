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
import { formatBatterName, formatPitcherName } from '../utils/handedness'

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

function formatPct(value) {
  if (value === null || value === undefined) return '-'
  const n = Number(value)
  if (Number.isNaN(n)) return '-'
  return `${(n * 100).toFixed(0)}%`
}

function formatNumber(value, digits = 1) {
  if (value === null || value === undefined) return '-'
  const n = Number(value)
  if (Number.isNaN(n)) return '-'
  return n.toFixed(digits)
}

function formatPctPoints(value, digits = 1) {
  if (value === null || value === undefined) return '-'
  const n = Number(value)
  if (Number.isNaN(n)) return '-'
  return `${n.toFixed(digits)}%`
}

async function fetchApiData(path) {
  const res = await fetch(`${API_BASE}${path}`)
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  const json = await res.json()
  if (json.code !== 200) {
    throw new Error(json.message || `Failed to fetch ${path}`)
  }
  return json.data
}

function formatCandidateBatter(candidate) {
  return formatBatterName({
    name: candidate.player_name,
    bats: candidate.bats,
  })
}

function formatCandidatePitcher(candidate) {
  return formatPitcherName({
    name: candidate.opposing_pitcher_name,
    throws: candidate.opposing_pitcher_throws,
  })
}

function groupCandidatesByGame(candidates = []) {
  if (!candidates.length) return []
  const map = new Map()
  for (const c of candidates) {
    const key = c.game_id ?? c.opposing_pitcher_name ?? c.venue ?? 'unknown'
    if (!map.has(key)) {
      map.set(key, {
        game_id: c.game_id,
        game_time: c.game_time,
        game_status: c.game_status,
        venue: c.venue,
        opposing_pitcher_name: c.opposing_pitcher_name,
        opposing_pitcher_throws: c.opposing_pitcher_throws,
        candidates: [],
      })
    }
    map.get(key).candidates.push(c)
  }
  return Array.from(map.values()).sort((a, b) => {
    if (!a.game_time && !b.game_time) return 0
    if (!a.game_time) return 1
    if (!b.game_time) return -1
    return a.game_time.localeCompare(b.game_time)
  })
}


function BettingPage({ onPlayerClick }) {
  const [data, setData] = useState(null)
  const [hitData, setHitData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [hitError, setHitError] = useState(null)
  const [refreshing, setRefreshing] = useState(false)
  // User toggle: when true, started/finished games appear in the list
  // (greyed out). Default false — once a game starts you can't bet on it
  // anymore, so hide it to reduce visual noise.
  const [showStarted, setShowStarted] = useState(false)

  const fetchCandidates = useCallback(async () => {
    const [edgeResult, hitResult] = await Promise.allSettled([
      fetchApiData('/betting/candidates'),
      fetchApiData('/betting/hit-candidates'),
    ])

    if (edgeResult.status === 'fulfilled') {
      setData(edgeResult.value)
      setError(null)
    } else {
      setError(`Could not reach backend: ${edgeResult.reason.message}`)
    }

    if (hitResult.status === 'fulfilled') {
      setHitData(hitResult.value)
      setHitError(null)
    } else {
      setHitData(null)
      setHitError(hitResult.reason.message)
    }
  }, [])

  // Initial load
  useEffect(() => {
    let mounted = true
    queueMicrotask(() => {
      fetchCandidates().finally(() => {
        if (mounted) setLoading(false)
      })
    })
    return () => {
      mounted = false
    }
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
    return groupCandidatesByGame(data?.candidates || [])
  }, [data?.candidates])
  const hitGameGroups = useMemo(() => {
    return groupCandidatesByGame(hitData?.candidates || [])
  }, [hitData?.candidates])

  // Filter games that have started unless the user explicitly opted in.
  const visibleGroups = useMemo(() => {
    if (showStarted) return gameGroups
    return gameGroups.filter(g => !gameHasStarted(g.game_status))
  }, [gameGroups, showStarted])
  const visibleHitGroups = useMemo(() => {
    if (showStarted) return hitGameGroups
    return hitGameGroups.filter(g => !gameHasStarted(g.game_status))
  }, [hitGameGroups, showStarted])

  // Counts for the toggle and subtitle. The backend can return useful
  // historical candidates for the audit log, but betting decisions only
  // matter before first pitch.
  const openCandidateCount = useMemo(() => (
    gameGroups
      .filter(g => !gameHasStarted(g.game_status))
      .reduce((sum, g) => sum + g.candidates.length, 0)
  ), [gameGroups])
  const hiddenStartedGameCount = useMemo(() => {
    const keys = new Set()
    for (const g of [...gameGroups, ...hitGameGroups]) {
      if (gameHasStarted(g.game_status)) {
        keys.add(g.game_id ?? g.opposing_pitcher_name ?? g.venue ?? 'unknown')
      }
    }
    return keys.size
  }, [gameGroups, hitGameGroups])
  const hiddenStartedCandidateCount = useMemo(() => (
    [...gameGroups, ...hitGameGroups]
      .filter(g => gameHasStarted(g.game_status))
      .reduce((sum, g) => sum + g.candidates.length, 0)
  ), [gameGroups, hitGameGroups])

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
              {hiddenStartedCandidateCount > 0 && (
                <> · {openCandidateCount} before first pitch</>
              )}
              {data.lineup_meta && (
                <> | {data.lineup_meta.lineup_counts?.confirmed || 0} confirmed / {data.lineup_meta.lineup_counts?.projected || 0} projected</>
              )}
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
        Composite score combines platoon, pitcher vulnerability, recent form,
        career batter-vs-pitcher history, and park factor. Projected lineup
        candidates must clear an extra {formatPct(data?.thresholds?.projected_lineup_edge_threshold)}
        {' '}quality floor before they appear. Confirmed lineups stay primary when
        MLB publishes them.
        {' '}
        Park factors:{' '}
        {data?.park_factor_meta?.source === 'baseball_savant' ? (
          <>Baseball Savant {data.park_factor_meta.year_range} rolling.</>
        ) : (
          <>static fallback.</>
        )}
        {' '}
        <em>Confirmed-lineup BvP hydration is capped so regeneration stays responsive.</em>
      </div>

      <div className="betting-formula-callout">
        <span>Edge formula</span>
        <strong>model probability - no-vig market probability = edge</strong>
        <p>
          Positive edge means our projection is stronger than the sportsbook's
          vig-free price. Negative edge means the market is richer than our model.
        </p>
      </div>

      {data?.lineup_meta && (
        <div className="betting-lineup-meta">
          <span>Lineups</span>
          <strong>{data.lineup_meta.mode || 'hybrid'}</strong>
          <p>
            Provider: {data.lineup_meta.provider || 'MLB confirmed only'} | status: {data.lineup_meta.status || 'disabled'}
            {data.lineup_meta.provider_meta?.lookback_days && (
              <> | {data.lineup_meta.provider_meta.lookback_days}-day lookback</>
            )}
            {data.lineup_meta.provider_meta?.confidence_floor != null && (
              <> | confidence floor {formatPct(data.lineup_meta.provider_meta.confidence_floor)}</>
            )}
          </p>
        </div>
      )}

      {hitError && (
        <div className="form-message form-message-error">
          Hit candidates unavailable: {hitError}
        </div>
      )}

      {!hitError && hitData && (
        <section className="hit-candidate-section">
          <div className="hit-candidate-heading">
            <div>
              <h3>Today's Hit Candidates</h3>
              <p>
                {(hitData.candidates || []).length} ranked by 1+ hit confidence
                {hitData.thresholds?.hit_form_days && (
                  <> | {hitData.thresholds.hit_form_days}-day form window</>
                )}
              </p>
            </div>
          </div>

          {(hitData.candidates || []).length === 0 && (
            <div className="betting-empty">No hit candidates available for this slate.</div>
          )}

          {(hitData.candidates || []).length > 0 && visibleHitGroups.length === 0 && !showStarted && (
            <div className="betting-empty">
              All current hit candidates are from games that already started.
            </div>
          )}

          {visibleHitGroups.map((group) => {
            const started = gameHasStarted(group.game_status)
            return (
              <div
                key={`hit-${group.game_id ?? group.opposing_pitcher_name}`}
                className={`betting-game-group hit-game-group${started ? ' started' : ''}`}
              >
                <div className="betting-game-header">
                  <span className="betting-game-time">{formatGameTime(group.game_time)}</span>
                  <span className="betting-game-separator">|</span>
                  <span className="betting-game-matchup">
                    {group.candidates[0]?.player_team || 'Lineup'} vs {formatCandidatePitcher(group)}
                  </span>
                  {group.venue && (
                    <span className="betting-game-venue"> @ {group.venue}</span>
                  )}
                  {group.game_status && (
                    <span className="betting-game-status">[{group.game_status}]</span>
                  )}
                  <span className="betting-game-count">
                    {group.candidates.length} hitter{group.candidates.length === 1 ? '' : 's'}
                  </span>
                </div>

                <div className="betting-grid hit-candidate-grid">
                  {group.candidates.map((c) => {
                    const hit = c.hit_candidate || {}
                    const stats = c.context_stats || {}
                    return (
                      <div
                        key={`hit-${c.hit_rank ?? c.rank}-${c.player_mlb_id}-${c.game_id}`}
                        className="betting-card hit-candidate-card"
                      >
                        <div className="betting-card-rank-and-score">
                          <span className="betting-rank">H#{c.hit_rank}</span>
                          <span className="hit-confidence">{formatPct(hit.hit_confidence ?? ((hit.score ?? 0) / 100))}</span>
                        </div>

                        <div className="betting-player">
                          <span
                            className="betting-player-name"
                            onClick={() =>
                              onPlayerClick && onPlayerClick({
                                name: c.player_name,
                                mlb_id: c.player_mlb_id,
                                team: c.player_team,
                                bats: c.bats,
                              })
                            }
                          >
                            {formatCandidateBatter(c)}
                          </span>
                          <span className="betting-team">{c.player_team}</span>
                          <span
                            className={`lineup-source-chip ${c.lineup_source === 'projected' ? 'projected' : 'confirmed'}`}
                          >
                            {c.lineup_source === 'projected' ? 'Projected' : 'Confirmed'}
                          </span>
                        </div>

                        <div className="betting-context">
                          <span className="betting-vs">vs</span>{' '}
                          <span className="betting-pitcher">{formatCandidatePitcher(c)}</span>
                          {c.batting_order && <> | <span className="betting-venue">batting #{c.batting_order}</span></>}
                        </div>

                        <div className="hit-card-metrics">
                          <span>score <strong>{formatNumber(hit.score, 1)}</strong></span>
                          <span>raw prob <strong>{formatPct(hit.hit_probability)}</strong></span>
                          <span>exp PA <strong>{formatNumber(hit.expected_pa, 1)}</strong></span>
                          <span>hit/PA <strong>{formatPct(hit.per_pa_hit_probability)}</strong></span>
                          <span>{stats.hit_form_days || hit.form_signal?.window_days || 5}d H/PA <strong>{formatPct(stats.rolling_hit_rate_per_pa)}</strong></span>
                        </div>

                        <div className="hit-card-metrics secondary">
                          <span>season H/PA <strong>{formatPct(stats.season_hit_rate_per_pa)}</strong></span>
                          <span>form signal <strong>{formatNumber(hit.form_signal?.value, 2)}</strong></span>
                          <span>K% <strong>{formatPctPoints(stats.hit_rolling_k_pct, 1)}</strong></span>
                        </div>

                        {(hit.reasons?.length > 0 || hit.risks?.length > 0) && (
                          <div className="hit-chip-row">
                            {hit.reasons?.map((reason, i) => (
                              <span key={`reason-${i}-${reason}`} className="hit-reason-chip">{reason}</span>
                            ))}
                            {hit.risks?.map((risk, i) => (
                              <span key={`risk-${i}-${risk}`} className="hit-risk-chip">{risk}</span>
                            ))}
                          </div>
                        )}
                      </div>
                    )
                  })}
                </div>
              </div>
            )
          })}
        </section>
      )}

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
      {hiddenStartedGameCount > 0 && (
        <div className="betting-toggle-row">
          <label>
            <input
              type="checkbox"
              checked={showStarted}
              onChange={(e) => setShowStarted(e.target.checked)}
            />
            {' '}Show {hiddenStartedCandidateCount} pick{hiddenStartedCandidateCount === 1 ? '' : 's'} from {hiddenStartedGameCount} game{hiddenStartedGameCount === 1 ? '' : 's'} already started or final
          </label>
        </div>
      )}

      {!error && data?.candidates?.length > 0 && visibleGroups.length === 0 && !showStarted && (
        <div className="betting-empty">
          All current candidates are from games that already started. Show
          started games to review them, or regenerate after later lineups post.
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
                {group.candidates[0]?.player_team || 'Lineup'} vs {formatCandidatePitcher(group)}
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
                          bats: c.bats,
                        })
                      }
                    >
                      {formatCandidateBatter(c)}
                    </span>
                    <span className="betting-team">{c.player_team}</span>
                    <span
                      className={`lineup-source-chip ${c.lineup_source === 'projected' ? 'projected' : 'confirmed'}`}
                      title={`${c.lineup_provider || 'lineup'}${c.lineup_edge_threshold != null ? ` | floor ${formatPct(c.lineup_edge_threshold)}` : ''}`}
                    >
                      {c.lineup_source === 'projected' ? 'Projected' : 'Confirmed'}
                    </span>
                  </div>

                  <div className="betting-context">
                    <span className="betting-vs">vs</span>{' '}
                    <span className="betting-pitcher">{formatCandidatePitcher(c)}</span>
                    {c.venue && <> · <span className="betting-venue">{c.venue}</span></>}
                    {c.batting_order && <> | <span className="betting-venue">batting #{c.batting_order}</span></>}
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
                      <span title="Lineup-risk edge floor">
                        edge floor: <strong>{formatPct(c.lineup_edge_threshold)}</strong>
                      </span>
                      {c.lineup_confidence != null && (
                        <span title={`Recent lineup sample: ${c.lineup_sample_size ?? '-'} of ${c.lineup_games_considered ?? '-'} games${c.lineup_split ? ` (${c.lineup_split})` : ''}`}>
                          lineup conf: <strong>{formatPct(c.lineup_confidence)}</strong>
                        </span>
                      )}
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
