/**
 * MatchupsPage.jsx - Starting Pitchers vs Projected Lineups
 * ==========================================================
 *
 * This component displays today's MLB games with starting pitchers and the
 * lineups they'll face. It's a self-contained page that manages its own
 * data fetching and state (unlike the dashboard, which gets data from App.jsx).
 *
 * Key React concepts demonstrated:
 * - Self-contained component with its own useEffect for data fetching
 * - Expand/collapse pattern using a Set stored in state
 * - Conditional rendering based on data availability
 * - Loading states per-section (global loading + per-game lineup loading)
 * - useCallback for memoized event handlers
 *
 * Data flow:
 *   1. On mount: fetch /matchups/today → get games with pitcher stats
 *   2. On game expand: fetch /matchups/lineup/{game_id} → get batting lineups
 *   3. On "vs Pitcher" toggle: fetch /matchups/vs-pitcher → get head-to-head stats
 *   4. On refresh: re-fetch everything, clear caches
 *
 * All data comes from the free MLB Stats API — no authentication or payment needed.
 */

import { useState, useEffect, useCallback } from 'react'
import { API_BASE } from '../config'

/**
 * Helper: Format an ISO datetime string into a readable local time.
 *
 * The MLB API returns game times in UTC (e.g., "2026-03-30T20:10:00Z").
 * This converts it to the user's local timezone and formats it nicely.
 *
 * @param {string} isoString - ISO 8601 datetime string from the API
 * @returns {string} Formatted time like "4:10 PM" or "TBD" if no time
 */
function formatGameTime(isoString) {
  if (!isoString) return 'TBD'
  try {
    const date = new Date(isoString)
    return date.toLocaleTimeString('en-US', {
      hour: 'numeric',
      minute: '2-digit',
      hour12: true,
    })
  } catch {
    return 'TBD'
  }
}

/**
 * Helper: Format a stat value for display.
 * Shows "-" for missing data, and keeps string values (like ".287") as-is.
 *
 * @param {*} value - The stat value from the API
 * @returns {string} Formatted display value
 */
function displayStat(value) {
  if (value === null || value === undefined || value === '-') return '-'
  return String(value)
}

export default function MatchupsPage({ season }) {
  // The season prop comes from App.jsx's global season toggle.
  // When null, we show the current year's label. When set (e.g., "2025"),
  // we show that year. This keeps the matchups page's labels consistent
  // with the rest of the app when the user toggles between seasons.
  const displayYear = season || new Date().getFullYear()
  // ---------------------------------------------------------------------------
  // STATE
  // ---------------------------------------------------------------------------

  // games: Array of game objects from /matchups/today. Each game has:
  //   { game_id, game_time, status, home_team, away_team,
  //     home_pitcher: { mlb_id, name, career_stats, season_stats },
  //     away_pitcher: { ... } }
  const [games, setGames] = useState([])

  // expandedGames: A Set of game_id values for games the user has clicked to expand.
  // We use a Set for O(1) lookups when checking if a game is expanded.
  // NOTE: React state must be immutable — we create a NEW Set each time
  // rather than modifying the existing one, so React detects the change.
  const [expandedGames, setExpandedGames] = useState(new Set())

  // lineups: Object mapping game_id → lineup data fetched from /matchups/lineup/{id}.
  // Acts as a cache — once fetched, we don't re-fetch until the user hits Refresh.
  const [lineups, setLineups] = useState({})

  // vsPitcherData: Object mapping game_id → vs-pitcher matchup stats.
  // Each entry is an object mapping batter_mlb_id → { has_data, stats }.
  const [vsPitcherData, setVsPitcherData] = useState({})

  // vsPitcherVisible: Set of game_ids where the "vs Pitcher" columns are toggled on.
  const [vsPitcherVisible, setVsPitcherVisible] = useState(new Set())

  // Loading states — separate flags for different loading scenarios:
  const [loading, setLoading] = useState(true)          // Initial page load
  const [refreshing, setRefreshing] = useState(false)    // Refresh button clicked
  const [loadingLineups, setLoadingLineups] = useState(new Set()) // Per-game lineup loading
  const [loadingVs, setLoadingVs] = useState(new Set())           // Per-game vs-pitcher loading

  // rangeStats: Object mapping "gameId-range" → { batter_mlb_id → stats }.
  const [rangeStats, setRangeStats] = useState({})

  // selectedRange: Object mapping gameId → selected range string ('season', '5day', '10day', '15day').
  const [selectedRange, setSelectedRange] = useState({})

  // loadingRange: Set of gameIds currently fetching range stats.
  const [loadingRange, setLoadingRange] = useState(new Set())

  const [error, setError] = useState(null)

  // ---------------------------------------------------------------------------
  // DATA FETCHING
  // ---------------------------------------------------------------------------

  /**
   * Fetch today's games with pitcher stats from the backend.
   * Called on mount and when the user clicks "Refresh".
   *
   * useCallback memoizes this function so it doesn't get recreated on every
   * render. This matters because it's used as a dependency in useEffect —
   * without useCallback, useEffect would re-run on every render.
   */
  const fetchGames = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/matchups/today`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const json = await res.json()
      if (json.code === 200 && json.data) {
        setGames(json.data.games || [])
        setError(null)
      } else {
        setError(json.message || 'Failed to load matchups')
      }
    } catch (err) {
      setError(`Could not connect to backend: ${err.message}`)
    }
  }, [])

  /**
   * Fetch lineup data for a specific game.
   * Called when the user expands a game card that hasn't been loaded yet.
   *
   * @param {number} gameId - The MLB gamePk to fetch lineups for
   */
  const fetchLineup = useCallback(async (gameId) => {
    // Mark this game as loading
    setLoadingLineups(prev => new Set([...prev, gameId]))

    try {
      const res = await fetch(`${API_BASE}/matchups/lineup/${gameId}`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const json = await res.json()
      if (json.code === 200 && json.data) {
        // Store the lineup in our cache object.
        // We use the functional form of setState (prev => ...) to avoid
        // stale closure issues when multiple lineups load concurrently.
        setLineups(prev => ({ ...prev, [gameId]: json.data }))
      }
    } catch (err) {
      console.error(`Failed to fetch lineup for game ${gameId}:`, err)
    } finally {
      // Remove from loading set
      setLoadingLineups(prev => {
        const next = new Set(prev)
        next.delete(gameId)
        return next
      })
    }
  }, [])

  /**
   * Fetch batter-vs-pitcher stats for a game's lineup.
   * Called when the user toggles "vs Pitcher" on a lineup table.
   *
   * @param {number} gameId - The game to fetch vs-pitcher data for
   * @param {string} side - "home" or "away" — which lineup to check
   */
  const fetchVsPitcher = useCallback(async (gameId, side) => {
    const lineup = lineups[gameId]
    if (!lineup) return

    // Determine which batters and which opposing pitcher to look up.
    // Home batters face the AWAY pitcher, and vice versa.
    const batters = lineup[`${side}_lineup`] || []
    const pitcherId = side === 'home'
      ? lineup.away_pitcher_id   // Home batters face away pitcher
      : lineup.home_pitcher_id   // Away batters face home pitcher

    if (!batters.length || !pitcherId) return

    // Build a composite key for loading state (gameId + side)
    const loadKey = `${gameId}-${side}`
    setLoadingVs(prev => new Set([...prev, loadKey]))

    try {
      const batterIds = batters.map(b => b.mlb_id).join(',')
      const res = await fetch(
        `${API_BASE}/matchups/vs-pitcher?batter_ids=${batterIds}&pitcher_id=${pitcherId}`
      )
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const json = await res.json()
      if (json.code === 200 && json.data) {
        // Convert the array of matchup results into a map keyed by batter_id
        // for O(1) lookups when rendering the lineup table.
        const matchupMap = {}
        for (const m of json.data.matchups || []) {
          matchupMap[m.mlb_id] = m
        }
        setVsPitcherData(prev => ({
          ...prev,
          [loadKey]: matchupMap,
        }))
      }
    } catch (err) {
      console.error(`Failed to fetch vs-pitcher for ${loadKey}:`, err)
    } finally {
      setLoadingVs(prev => {
        const next = new Set(prev)
        next.delete(loadKey)
        return next
      })
    }
  }, [lineups])

  /**
   * Fetch batter stats for a specific date range (5day, 10day, 15day).
   * Season stats are already available from the lineup endpoint, so this
   * is only called for rolling window ranges.
   */
  const fetchRangeStats = useCallback(async (gameId, range) => {
    const cacheKey = `${gameId}-${range}`
    if (rangeStats[cacheKey]) return

    setLoadingRange(prev => new Set([...prev, gameId]))

    try {
      const res = await fetch(`${API_BASE}/matchups/lineup-stats/${gameId}?range=${range}`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const json = await res.json()
      if (json.code === 200 && json.data) {
        setRangeStats(prev => ({ ...prev, [cacheKey]: json.data.stats }))
      }
    } catch (err) {
      console.error(`Failed to fetch ${range} stats for game ${gameId}:`, err)
    } finally {
      setLoadingRange(prev => {
        const next = new Set(prev)
        next.delete(gameId)
        return next
      })
    }
  }, [rangeStats])

  // ---------------------------------------------------------------------------
  // EFFECTS
  // ---------------------------------------------------------------------------

  /**
   * useEffect with [] dependency array runs ONCE when the component mounts.
   * This is where we load the initial data — today's games and pitcher stats.
   */
  useEffect(() => {
    fetchGames().finally(() => setLoading(false))
  }, [fetchGames])

  // ---------------------------------------------------------------------------
  // EVENT HANDLERS
  // ---------------------------------------------------------------------------

  /**
   * Handle clicking a game card to expand/collapse it.
   * If expanding for the first time, also fetches the lineup data.
   */
  const handleToggleGame = (gameId) => {
    setExpandedGames(prev => {
      const next = new Set(prev)
      if (next.has(gameId)) {
        // Collapse — just remove from set, keep cached data
        next.delete(gameId)
      } else {
        // Expand — add to set and fetch lineup if not cached
        next.add(gameId)
        if (!lineups[gameId]) {
          fetchLineup(gameId)
        }
      }
      return next
    })
  }

  /**
   * Handle the "Refresh" button click.
   * Re-fetches the schedule and clears all cached lineup/vs-pitcher data,
   * then re-fetches lineups for any currently expanded games.
   */
  const handleRefresh = async () => {
    setRefreshing(true)
    setLineups({})
    setVsPitcherData({})
    setVsPitcherVisible(new Set())
    setRangeStats({})
    setSelectedRange({})
    setLoadingRange(new Set())
    await fetchGames()

    // Re-fetch lineups for any games that are currently expanded
    const expandedIds = [...expandedGames]
    await Promise.all(expandedIds.map(id => fetchLineup(id)))

    setRefreshing(false)
  }

  /**
   * Toggle the "vs Pitcher" column for a specific lineup.
   * Fetches the data on first toggle, then just shows/hides the column.
   */
  const handleToggleVsPitcher = (gameId, side) => {
    const key = `${gameId}-${side}`

    setVsPitcherVisible(prev => {
      const next = new Set(prev)
      if (next.has(key)) {
        next.delete(key)
      } else {
        next.add(key)
        // Fetch vs-pitcher data if not already cached
        if (!vsPitcherData[key]) {
          fetchVsPitcher(gameId, side)
        }
      }
      return next
    })
  }

  /**
   * Handle selecting a stat range for a game's lineup table.
   */
  const handleSelectRange = (gameId, range) => {
    setSelectedRange(prev => ({ ...prev, [gameId]: range }))
    if (range !== 'season') {
      fetchRangeStats(gameId, range)
    }
  }

  // ---------------------------------------------------------------------------
  // RENDER HELPERS
  // ---------------------------------------------------------------------------

  /**
   * Render a pitcher's stat block (career + season stats).
   * Used for both the home and away pitcher in each game card.
   */
  const renderPitcherPanel = (pitcher, label) => {
    if (!pitcher || !pitcher.mlb_id) {
      // Pitcher TBD — not yet announced
      return (
        <div className="pitcher-panel">
          <div className="pitcher-label">{label}</div>
          <div className="pitcher-name">TBD</div>
          <div className="pitcher-tbd">Pitcher not yet announced</div>
        </div>
      )
    }

    const career = pitcher.career_stats || {}
    const season = pitcher.season_stats || {}

    return (
      <div className="pitcher-panel">
        <div className="pitcher-label">{label}</div>
        {/* Pitcher headshot from the MLB CDN — free, no auth needed.
            The URL pattern is consistent for all MLB player photos. */}
        <div className="pitcher-header">
          <img
            className="pitcher-headshot"
            src={`https://img.mlbstatic.com/mlb-photos/image/upload/d_people:generic:headshot:67:current.png/w_80,q_auto:best/v1/people/${pitcher.mlb_id}/headshot/silo/current`}
            alt={pitcher.name}
            onError={(e) => { e.target.style.display = 'none' }}
          />
          <div>
            <div className="pitcher-name">{pitcher.name}</div>
          </div>
        </div>

        {/* Career stats — the pitcher's full MLB career numbers */}
        <div className="pitcher-stats-section">
          <div className="pitcher-stats-label">Career</div>
          <div className="pitcher-stat-row">
            <span className="stat-item"><span className="stat-key">W-L</span> {displayStat(career.wins)}-{displayStat(career.losses)}</span>
            <span className="stat-item"><span className="stat-key">ERA</span> {displayStat(career.era)}</span>
            <span className="stat-item"><span className="stat-key">WHIP</span> {displayStat(career.whip)}</span>
            <span className="stat-item"><span className="stat-key">K</span> {displayStat(career.strikeouts)}</span>
            <span className="stat-item"><span className="stat-key">IP</span> {displayStat(career.innings_pitched)}</span>
          </div>
        </div>

        {/* Season stats — current year performance (may be empty early in season).
            The label uses displayYear so it stays in sync with the global season
            toggle in App.jsx (e.g., shows "2025 Season" when viewing historical data). */}
        <div className="pitcher-stats-section">
          <div className="pitcher-stats-label">{displayYear} Season</div>
          <div className="pitcher-stat-row">
            <span className="stat-item"><span className="stat-key">W-L</span> {displayStat(season.wins)}-{displayStat(season.losses)}</span>
            <span className="stat-item"><span className="stat-key">ERA</span> {displayStat(season.era)}</span>
            <span className="stat-item"><span className="stat-key">WHIP</span> {displayStat(season.whip)}</span>
            <span className="stat-item"><span className="stat-key">K</span> {displayStat(season.strikeouts)}</span>
            <span className="stat-item"><span className="stat-key">IP</span> {displayStat(season.innings_pitched)}</span>
          </div>
        </div>
      </div>
    )
  }

  /**
   * Render a lineup table for one side of a game (home or away).
   * Shows each batter in order with current season stats (default).
   * Includes toggle buttons for 5-day, 10-day, and 15-day rolling windows.
   * Optionally shows "vs Pitcher" stats when that toggle is active.
   */
  const renderLineupTable = (gameId, side, teamName, lineupData) => {
    const announced = lineupData?.[`${side}_lineup_announced`]
    const batters = lineupData?.[`${side}_lineup`] || []
    const vsKey = `${gameId}-${side}`
    const showVs = vsPitcherVisible.has(vsKey)
    const vsData = vsPitcherData[vsKey] || {}
    const vsLoading = loadingVs.has(vsKey)

    // Current range selection for this game (default: 'season')
    const currentRange = selectedRange[gameId] || 'season'
    const isRangeLoading = loadingRange.has(gameId)
    const rangeCacheKey = `${gameId}-${currentRange}`
    const rangeStatsData = rangeStats[rangeCacheKey]

    // Determine which pitcher this lineup faces (for the toggle button label)
    const game = games.find(g => g.game_id === gameId)
    const opposingPitcher = side === 'home'
      ? game?.away_pitcher?.name
      : game?.home_pitcher?.name

    /**
     * Get the stats object for a batter based on the selected range.
     * For 'season', use the season_stats from the lineup endpoint.
     * For rolling windows, use the cached range stats if available.
     */
    const getStatsForBatter = (batter) => {
      if (currentRange === 'season') {
        return batter.season_stats || {}
      }
      if (rangeStatsData && rangeStatsData[batter.mlb_id]) {
        return rangeStatsData[batter.mlb_id]
      }
      return {}
    }

    const ranges = [
      { key: 'season', label: 'Season' },
      { key: '5day', label: '5 Day' },
      { key: '10day', label: '10 Day' },
      { key: '15day', label: '15 Day' },
    ]

    return (
      <div className="lineup-section">
        <div className="lineup-header">
          <h4 className="lineup-title">
            {teamName} Lineup
            {opposingPitcher && ` vs ${opposingPitcher}`}
          </h4>
          <div className="lineup-header-controls">
            {announced && batters.length > 0 && (
              <div className="range-buttons">
                {ranges.map(r => (
                  <button
                    key={r.key}
                    className={`range-btn${currentRange === r.key ? ' active' : ''}`}
                    onClick={() => handleSelectRange(gameId, r.key)}
                    disabled={isRangeLoading}
                  >
                    {r.label}
                  </button>
                ))}
              </div>
            )}
            {announced && batters.length > 0 && (
              <button
                className={`vs-pitcher-toggle${showVs ? ' active' : ''}`}
                onClick={() => handleToggleVsPitcher(gameId, side)}
                disabled={vsLoading}
              >
                {vsLoading ? 'Loading...' : showVs ? 'Hide vs Pitcher' : 'Show vs Pitcher'}
              </button>
            )}
          </div>
        </div>

        {!announced ? (
          <div className="lineup-tbd">
            Lineup TBD — lineups are typically announced 1-3 hours before game time.
            Use the Refresh button to check for updates.
          </div>
        ) : batters.length === 0 ? (
          <div className="lineup-tbd">No lineup data available.</div>
        ) : (
          <div className="lineup-table-wrapper">
            <table className={`lineup-table${isRangeLoading ? ' loading' : ''}`}>
              <thead>
                <tr>
                  <th>#</th>
                  <th>Name</th>
                  <th>Pos</th>
                  <th>AVG</th>
                  <th>HR</th>
                  <th>RBI</th>
                  <th>OBP</th>
                  <th>OPS</th>
                  <th>K</th>
                  <th>AB</th>
                  {showVs && <>
                    <th className="vs-divider">vs AVG</th>
                    <th>vs AB</th>
                    <th>vs HR</th>
                    <th>vs K</th>
                  </>}
                </tr>
              </thead>
              <tbody>
                {batters.map((batter) => {
                  const stats = getStatsForBatter(batter)
                  const vs = vsData[batter.mlb_id]

                  return (
                    <tr key={batter.mlb_id}>
                      <td className="lineup-order">{batter.batting_order}</td>
                      <td className="lineup-name">{batter.name}</td>
                      <td className="lineup-pos">{batter.position}</td>
                      <td>{displayStat(stats.avg)}</td>
                      <td>{displayStat(stats.home_runs)}</td>
                      <td>{displayStat(stats.rbi)}</td>
                      <td>{displayStat(stats.obp)}</td>
                      <td>{displayStat(stats.ops)}</td>
                      <td>{displayStat(stats.strikeouts)}</td>
                      <td>{displayStat(stats.at_bats)}</td>
                      {showVs && (
                        vs?.has_data ? <>
                          <td className="vs-divider">{displayStat(vs.stats.avg)}</td>
                          <td>{displayStat(vs.stats.at_bats)}</td>
                          <td>{displayStat(vs.stats.home_runs)}</td>
                          <td>{displayStat(vs.stats.strikeouts)}</td>
                        </> : <>
                          <td className="vs-divider vs-no-data" colSpan={4}>
                            No matchup history
                          </td>
                        </>
                      )}
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    )
  }

  // ---------------------------------------------------------------------------
  // MAIN RENDER
  // ---------------------------------------------------------------------------

  // Loading spinner while initial data loads
  if (loading) {
    return (
      <div className="matchups-page">
        <div className="matchups-loading">Loading today's matchups...</div>
      </div>
    )
  }

  return (
    <div className="matchups-page">
      {/* Page header with date and refresh button */}
      <div className="matchups-header">
        <h2 className="matchups-title">
          Today's Starting Pitchers & Lineups
        </h2>
        <button
          className="matchups-refresh-btn"
          onClick={handleRefresh}
          disabled={refreshing}
        >
          {refreshing ? 'Refreshing...' : 'Refresh Lineups'}
        </button>
      </div>

      {/* Error message if the backend request failed */}
      {error && (
        <div className="form-message form-message-error">{error}</div>
      )}

      {/* No games message */}
      {!error && games.length === 0 && (
        <div className="matchups-no-games">
          No MLB games scheduled for today.
        </div>
      )}

      {/* Game cards — one card per game with pitcher stats and expandable lineups */}
      <div className="matchups-grid">
        {games.map((game) => {
          const isExpanded = expandedGames.has(game.game_id)
          const lineupData = lineups[game.game_id]
          const isLoadingLineup = loadingLineups.has(game.game_id)

          return (
            <div key={game.game_id} className={`game-card${isExpanded ? ' expanded' : ''}`}>
              {/* Game card header — shows teams, time, and acts as expand/collapse toggle */}
              <div
                className="game-card-header"
                onClick={() => handleToggleGame(game.game_id)}
              >
                <div className="game-teams">
                  <span className="team-name">{game.away_team}</span>
                  <span className="game-at">@</span>
                  <span className="team-name">{game.home_team}</span>
                </div>
                <div className="game-info">
                  <span className="game-time">{formatGameTime(game.game_time)}</span>
                  <span className="game-status">{game.status}</span>
                  {/* Expand/collapse arrow indicator */}
                  <span className={`expand-arrow${isExpanded ? ' expanded' : ''}`}>
                    &#9660;
                  </span>
                </div>
              </div>

              {/* Pitcher panels — always visible (not just when expanded).
                  Shows both starting pitchers side-by-side with their stats. */}
              <div className="pitcher-panels">
                {renderPitcherPanel(game.away_pitcher, 'Away')}
                <div className="pitcher-vs">VS</div>
                {renderPitcherPanel(game.home_pitcher, 'Home')}
              </div>

              {/* Expanded section — lineup tables (only shown when game is expanded) */}
              {isExpanded && (
                <div className="game-lineups">
                  {isLoadingLineup ? (
                    <div className="lineup-loading">Loading lineups...</div>
                  ) : (
                    <>
                      {/* Away team lineup (batting against the HOME pitcher) */}
                      {renderLineupTable(game.game_id, 'away', game.away_team, lineupData)}
                      {/* Home team lineup (batting against the AWAY pitcher) */}
                      {renderLineupTable(game.game_id, 'home', game.home_team, lineupData)}
                    </>
                  )}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
