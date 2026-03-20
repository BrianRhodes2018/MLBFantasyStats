/**
 * App.jsx - Root Application Component
 * ======================================
 *
 * This is the top-level React component that orchestrates the entire app.
 * It manages all application state and passes data down to child components.
 *
 * Key React concepts demonstrated:
 * - useState: Creates reactive state variables that trigger re-renders when updated
 * - useEffect: Runs side effects (like API calls) after the component mounts
 * - Props: Data passed from parent to child components (one-way data flow)
 * - Callbacks: Functions passed to children so they can notify the parent of events
 * - Nullish coalescing (??): Used to show search results OR all players
 * - Conditional rendering: Different table titles based on search state
 *
 * Data flow:
 *   App (state) --> StatsPanel (displays averages + team stats)
 *              --> PlayerSearch (filter controls, triggers batter search via callback)
 *              --> PitcherSearch (filter controls, triggers pitcher search via callback)
 *              --> PlayerForm (adds player, triggers refresh via callback)
 *              --> PlayerTable (displays batters — either all or filtered)
 *              --> PitcherTable (displays pitchers — either all or filtered)
 *
 * API endpoints consumed:
 *   GET /players/            -> All players (raw data from database)
 *   GET /players/stats       -> League-wide averages (Polars aggregation)
 *   GET /players/computed    -> Per-player computed stats (Polars expressions)
 *   GET /players/team-stats  -> Team-level aggregations (Polars group_by)
 *   GET /players/filterable-stats -> Metadata for dynamic batter search filter generation
 *   GET /players/search?...  -> Filtered player results (Polars .filter())
 *   GET /pitchers/           -> All pitchers (raw data from database)
 *   GET /pitchers/computed   -> Per-pitcher computed stats (K/9, BB/9, etc.)
 *   GET /pitchers/filterable-stats -> Metadata for dynamic pitcher search filter generation
 *   GET /pitchers/search?... -> Filtered pitcher results (Polars .filter())
 */

import { useState, useEffect } from 'react'
import PlayerTable from './components/PlayerTable'
import PitcherTable from './components/PitcherTable'
import PlayerForm from './components/PlayerForm'
import PlayerSearch from './components/PlayerSearch'
import PitcherSearch from './components/PitcherSearch'
import StatsPanel from './components/StatsPanel'
import LeagueSelector from './components/LeagueSelector'
import PlayerModal from './components/PlayerModal'
import PlayerComparison from './components/PlayerComparison'
import { fuzzyNameMatch } from './utils/fuzzyMatch'
// TimePeriodSelector is now rendered INSIDE PlayerSearch/PitcherSearch
// rather than as a standalone component in App.jsx. This keeps the
// time period toggle visually grouped with the stat filter panel.

// API_BASE is the backend URL prefix. Empty string in dev (uses Vite proxy),
// full URL in production (e.g., "https://your-app.onrender.com").
// See config.js for details on how this works with Vite environment variables.
import { API_BASE } from './config'

function App() {
  // ---------------------------------------------------------------------------
  // STATE DECLARATIONS
  // ---------------------------------------------------------------------------
  // useState returns [currentValue, setterFunction].
  // When you call the setter, React re-renders this component with the new value.

  const [players, setPlayers] = useState([])         // Array of all player objects from the DB
  const [stats, setStats] = useState(null)            // League average stats (Polars)
  const [computed, setComputed] = useState([])         // Per-player computed stats (Polars)
  const [teamStats, setTeamStats] = useState([])       // Team-level aggregations (Polars)
  const [filterMeta, setFilterMeta] = useState(null)   // Search filter metadata (stat names, ranges, positions)
  const [searchResults, setSearchResults] = useState(null)  // null = no search active, array = filtered results
  const [loading, setLoading] = useState(true)         // Loading indicator for initial fetch
  const [fetchError, setFetchError] = useState(null)    // Error message if data fetch fails

  // Pitcher state
  const [pitchers, setPitchers] = useState([])         // Array of all pitcher objects
  const [pitcherComputed, setPitcherComputed] = useState([])  // Pitcher computed stats (K/9, BB/9, etc.)
  const [pitcherFilterMeta, setPitcherFilterMeta] = useState(null)  // Search filter metadata for pitchers (stat names, ranges, positions)
  const [pitcherSearchResults, setPitcherSearchResults] = useState(null)  // null = no search active, array = filtered pitcher results

  // Position filter state - controls which players/pitchers are shown
  // Options: 'Batters', 'Pitchers', 'C', '1B', '2B', '3B', 'SS', '2B/SS', '1B/3B', 'OF', 'SP', 'RP'
  const [positionFilter, setPositionFilter] = useState('Batters')

  // Team filter state - filters the table to show only players/pitchers on a specific team.
  // '' means "All Teams" (no filter). Works alongside the position filter so
  // you can view e.g., "All Shortstops on the Yankees" by combining both.
  const [teamFilter, setTeamFilter] = useState('')

  // Name search state — drives the fuzzy name search input in the header bar.
  // As the user types, the table filters in real-time using fuzzyNameMatch().
  // This is a CLIENT-SIDE filter (no API call) applied on top of the existing data.
  const [nameSearch, setNameSearch] = useState('')

  // ---------------------------------------------------------------------------
  // ROLLING STATS STATE (Time Period Feature)
  // ---------------------------------------------------------------------------
  // These state variables power the "Last 5 / 10 / 15 / 30 days" feature.
  //
  // activePeriod: Which time window is selected.
  //   - 'season' means show full-season stats (the default, existing behavior)
  //   - 5, 10, 15, 30 means show rolling stats over that many days
  //
  // rollingBatters / rollingPitchers: The rolling stats data fetched from the
  //   /players/rolling-stats and /pitchers/rolling-stats endpoints.
  //   These replace the normal players/pitchers data when a rolling period is active.
  //
  // rollingLoading: True while fetching rolling data, used to disable the
  //   TimePeriodSelector buttons and show a loading indicator.
  const [activePeriod, setActivePeriod] = useState('season')
  const [rollingBatters, setRollingBatters] = useState([])
  const [rollingPitchers, setRollingPitchers] = useState([])
  const [rollingLoading, setRollingLoading] = useState(false)

  // --- Fantasy League State ---
  // fantasyLeagues: Array of saved ESPN league objects from the database.
  //   Each has: id, league_name, league_id, season_year, scoring_settings.
  // activeLeagueId: The database ID of the currently selected league (null = none).
  //   When set, fantasy points are fetched and displayed in the table.
  // fantasyBatterPts: Array of {id, name, fantasy_pts} for all batters,
  //   computed using the active league's scoring rules.
  // fantasyPitcherPts: Same for pitchers.
  const [fantasyLeagues, setFantasyLeagues] = useState([])
  const [activeLeagueId, setActiveLeagueId] = useState(null)
  const [fantasyBatterPts, setFantasyBatterPts] = useState([])
  const [fantasyPitcherPts, setFantasyPitcherPts] = useState([])

  // Player Detail Modal state
  // null = no modal open, object = the player/pitcher whose detail modal is shown.
  const [modalPlayer, setModalPlayer] = useState(null)
  const [modalPlayerType, setModalPlayerType] = useState(null)  // 'batter' | 'pitcher'

  // ---------------------------------------------------------------------------
  // PLAYER COMPARISON STATE
  // ---------------------------------------------------------------------------
  const [comparisonPlayers, setComparisonPlayers] = useState([])
  const [comparisonType, setComparisonType] = useState(null)   // 'batter' | 'pitcher' | null
  const [comparisonOpen, setComparisonOpen] = useState(false)

  // Derived set for O(1) lookups when rendering Compare buttons in table rows
  const comparisonIds = new Set(comparisonPlayers.map(p => p.id ?? p.player_id))

  // ---------------------------------------------------------------------------
  // DATA FETCHING
  // ---------------------------------------------------------------------------

  /**
   * Helper to fetch a single endpoint and parse its JSON response.
   *
   * Wraps fetch() + .json() in a try/catch so that a failure in one
   * endpoint doesn't prevent others from loading. Returns null on failure
   * and logs the error to the console for debugging.
   *
   * Prepends API_BASE to the URL so this works in both environments:
   * - Dev: API_BASE="" → fetch("/players/") → Vite proxy → localhost:8000
   * - Prod: API_BASE="https://..." → fetch("https://.../players/")
   *
   * @param {string} url - The endpoint path (e.g., "/players/")
   * @returns {Promise<any|null>} The parsed JSON data, or null if the fetch failed
   */
  const safeFetch = async (url) => {
    try {
      const res = await fetch(`${API_BASE}${url}`)
      if (!res.ok) {
        console.error(`Fetch ${url} returned HTTP ${res.status}`)
        return null
      }
      return await res.json()
    } catch (err) {
      console.error(`Fetch ${url} failed:`, err)
      return null
    }
  }

  /**
   * Fetch all data from the backend API.
   *
   * Promise.all() runs multiple fetch requests IN PARALLEL (not one after another).
   * This is faster than awaiting each one sequentially. All five requests go out
   * at the same time, and we wait for all of them to finish.
   *
   * Each fetch is wrapped in safeFetch() so a single endpoint failure won't
   * prevent the rest of the app from loading. For example, if /players/stats
   * fails, the stats panel will show "no data" but the player table and
   * search panel will still work.
   *
   * The fetch() calls use relative URLs (e.g., "/players/") which work because
   * the Vite proxy forwards them to http://localhost:8000. See vite.config.js.
   */
  const fetchData = async () => {
    try {
      // Fire all requests simultaneously — including pitcher filterable-stats
      // so the PitcherSearch filter panel can render as soon as the page loads.
      // safeFetch returns null on failure instead of throwing, so one
      // broken endpoint won't take down the others.
      const [playersData, statsData, computedData, teamStatsData, filterMetaData, pitchersData, pitcherComputedData, pitcherFilterMetaData, leaguesData] = await Promise.all([
        safeFetch('/players/'),
        safeFetch('/players/stats'),
        safeFetch('/players/computed'),
        safeFetch('/players/team-stats'),
        safeFetch('/players/filterable-stats'),
        safeFetch('/pitchers/'),
        safeFetch('/pitchers/computed'),
        safeFetch('/pitchers/filterable-stats'),  // Metadata for pitcher search filters (stat names, min/max ranges, positions, teams)
        safeFetch('/fantasy/leagues'),             // Saved ESPN fantasy leagues (for league selector dropdown)
      ])

      // Update state with whatever data we got. Each setter triggers a
      // re-render, but React batches these updates so only one re-render happens.
      // The || fallbacks ensure state stays at a safe default if the fetch failed.
      setPlayers(playersData || [])
      setStats(statsData)
      setComputed(computedData || [])
      setTeamStats(teamStatsData || [])
      setPitchers(pitchersData || [])
      setPitcherComputed(pitcherComputedData || [])
      // filterMetaData is an ApiResponse: { code, message, data: { stats, positions, teams } }
      // We extract .data to get the actual metadata object.
      setFilterMeta(filterMetaData?.data || null)
      // Same extraction for pitcher filter metadata — used by PitcherSearch component
      setPitcherFilterMeta(pitcherFilterMetaData?.data || null)
      // Fantasy leagues — the leaguesData response is wrapped in ApiResponse
      // so we extract .data to get the array of league objects.
      setFantasyLeagues(leaguesData?.data || [])

      // If ALL fetches returned null, something is fundamentally wrong
      // (backend not running, proxy misconfigured, etc.)
      if (!playersData && !statsData && !filterMetaData) {
        setFetchError('Could not connect to the backend API. Make sure the FastAPI server is running on port 8000.')
      } else {
        setFetchError(null)
      }
    } catch (error) {
      console.error('Failed to fetch data:', error)
      setFetchError(`Unexpected error: ${error.message}`)
    } finally {
      setLoading(false)
    }
  }

  // ---------------------------------------------------------------------------
  // EFFECTS
  // ---------------------------------------------------------------------------

  /**
   * useEffect with an empty dependency array [] runs ONCE after the component
   * first renders (mounts). This is the React equivalent of "on page load".
   */
  useEffect(() => {
    fetchData()
  }, [])  // Empty array = run once on mount

  /**
   * Fetch fantasy points whenever the active league changes.
   *
   * This mirrors the pattern used for rolling stats: when the user selects
   * a league from the dropdown, we fetch the computed fantasy points for
   * all batters and pitchers in parallel from the backend.
   *
   * When activeLeagueId is null ("No League" selected), we clear the
   * fantasy points arrays so the Fantasy Pts column disappears from the tables.
   *
   * The backend computes points using Polars expressions:
   *   fantasy_pts = SUM(player_stat * league_point_value) per scored category
   */
  useEffect(() => {
    const fetchFantasyPoints = async () => {
      if (!activeLeagueId) {
        // No league selected — clear fantasy points so the column disappears
        setFantasyBatterPts([])
        setFantasyPitcherPts([])
        return
      }

      // Fetch fantasy points for batters and pitchers in parallel
      const [batterPts, pitcherPts] = await Promise.all([
        safeFetch(`/fantasy/points/batters/${activeLeagueId}`),
        safeFetch(`/fantasy/points/pitchers/${activeLeagueId}`),
      ])

      // The response is a direct array of {id, name, fantasy_pts} objects
      // (not wrapped in ApiResponse) — same pattern as /players/computed
      setFantasyBatterPts(batterPts || [])
      setFantasyPitcherPts(pitcherPts || [])
    }

    fetchFantasyPoints()
  }, [activeLeagueId])  // Re-run whenever the selected league changes

  // ---------------------------------------------------------------------------
  // EVENT HANDLERS
  // ---------------------------------------------------------------------------

  /**
   * Callback passed to PlayerForm. Called after a new player is successfully added.
   * Re-fetches all data so the table, stats, and computed values update automatically.
   * Also clears any active search results since the data changed.
   *
   * This is the "lifting state up" pattern: the child (PlayerForm) doesn't manage
   * the player list — it just notifies the parent (App) that something changed,
   * and the parent decides what to do (re-fetch everything).
   */
  const handlePlayerAdded = () => {
    setSearchResults(null)  // Clear search results since data changed
    fetchData()
  }

  /**
   * Callback passed to PlayerSearch. Called when search results come back.
   *
   * The search API returns: { results: [...], count: N }
   * We store the results array in searchResults state. When this is non-null,
   * the PlayerTable shows filtered results instead of all players.
   *
   * @param {Object} data - The API response data: { results: [...], count: N }
   */
  const handleSearchResults = (data) => {
    setSearchResults(data.results)
  }

  /**
   * Callback passed to PlayerSearch. Called when user clicks "Clear".
   * Sets searchResults back to null, which makes PlayerTable show all players.
   */
  const handleClearSearch = () => {
    setSearchResults(null)
  }

  /**
   * Callback passed to PlayerTable. Called after a player is successfully updated.
   * Re-fetches all data so the table, stats, and computed values reflect the changes.
   * Also clears any active search results since the underlying data changed.
   */
  const handlePlayerUpdated = () => {
    setSearchResults(null)
    fetchData()
  }

  /**
   * Callback passed to PitcherTable. Called after a pitcher is successfully updated.
   * Re-fetches all data so the pitcher table and computed values reflect the changes.
   * Also clears any active pitcher search results since the underlying data changed.
   */
  const handlePitcherUpdated = () => {
    setPitcherSearchResults(null)  // Clear pitcher search results since data changed
    fetchData()
  }

  // --- Fantasy League Callbacks ---

  /**
   * Called when the user selects a different league in the LeagueSelector dropdown.
   * Setting activeLeagueId triggers the useEffect above which fetches fantasy points.
   * Setting to null (when "No League" is chosen) clears fantasy points.
   *
   * @param {number|null} leagueId - Database ID of the selected league, or null
   */
  const handleLeagueChange = (leagueId) => {
    setActiveLeagueId(leagueId)
  }

  /**
   * Called after a new ESPN league is successfully connected.
   * Re-fetches the leagues list so the dropdown updates to include the new league.
   */
  const handleLeagueAdded = () => {
    safeFetch('/fantasy/leagues').then(data => {
      setFantasyLeagues(data?.data || [])
    })
  }

  /**
   * Called after a league is removed from the database.
   * Clears the active selection and fantasy points, then refreshes the leagues list.
   */
  const handleLeagueRemoved = () => {
    setActiveLeagueId(null)
    setFantasyBatterPts([])
    setFantasyPitcherPts([])
    safeFetch('/fantasy/leagues').then(data => {
      setFantasyLeagues(data?.data || [])
    })
  }

  /**
   * Callback passed to PitcherSearch. Called when pitcher search results come back.
   *
   * This mirrors handleSearchResults for batters. The pitcher search API returns:
   * { results: [...], count: N }
   * We store the results array in pitcherSearchResults state. When this is non-null,
   * the PitcherTable shows filtered results instead of all pitchers.
   *
   * @param {Object} data - The API response data: { results: [...], count: N }
   */
  const handlePitcherSearchResults = (data) => {
    setPitcherSearchResults(data.results)
  }

  /**
   * Callback passed to PitcherSearch. Called when user clicks "Clear".
   * Sets pitcherSearchResults back to null, which makes PitcherTable show all pitchers.
   * This mirrors handleClearSearch for batters.
   */
  const handleClearPitcherSearch = () => {
    setPitcherSearchResults(null)
  }

  // ---------------------------------------------------------------------------
  // ROLLING STATS HANDLERS
  // ---------------------------------------------------------------------------

  /**
   * Fetch rolling (time-period) stats from the backend API.
   *
   * When the user clicks a time period button (e.g., "Last 15 Days"), this
   * function fetches aggregated game log data for that window. The backend
   * filters game logs by date, groups by player, and computes rate stats
   * (batting average, ERA, etc.) over just that window.
   *
   * We fetch BOTH batter and pitcher rolling stats at once (in parallel)
   * so the data is ready regardless of which position filter is active.
   *
   * @param {number} days - Number of days to look back (5, 10, 15, or 30)
   */
  const fetchRollingStats = async (days) => {
    setRollingLoading(true)
    try {
      // Fetch batter and pitcher rolling stats in parallel.
      // The `days` parameter tells the backend how far back to look.
      const [batterData, pitcherData] = await Promise.all([
        safeFetch(`/players/rolling-stats?days=${days}`),
        safeFetch(`/pitchers/rolling-stats?days=${days}`),
      ])
      setRollingBatters(batterData || [])
      setRollingPitchers(pitcherData || [])
    } catch (error) {
      console.error('Failed to fetch rolling stats:', error)
      setRollingBatters([])
      setRollingPitchers([])
    } finally {
      setRollingLoading(false)
    }
  }

  /**
   * Handle time period selection changes.
   *
   * When the user clicks a period button:
   * - 'season': Switch back to full-season stats (clear rolling data)
   * - 5/10/15/30: Fetch rolling stats for that many days
   *
   * Also clears any active search results since rolling stats are a
   * different dataset that doesn't support the search filters.
   *
   * @param {string|number} period - 'season' or a number of days (5, 10, 15, 30)
   */
  const handlePeriodChange = (period) => {
    setActivePeriod(period)
    setSearchResults(null)          // Clear batter search when switching periods
    setPitcherSearchResults(null)   // Clear pitcher search when switching periods

    if (period === 'season') {
      // Switch back to full-season view — clear rolling data
      setRollingBatters([])
      setRollingPitchers([])
    } else {
      // Fetch rolling stats for the selected number of days
      fetchRollingStats(period)
    }
  }

  // ---------------------------------------------------------------------------
  // PLAYER DETAIL MODAL HANDLERS
  // ---------------------------------------------------------------------------

  /**
   * Open the player detail modal for a batter.
   * Called when a player name is clicked in PlayerTable.
   * @param {Object} player - The full player object from the table row
   */
  const handlePlayerClick = (player) => {
    setModalPlayer(player)
    setModalPlayerType('batter')
  }

  /**
   * Open the player detail modal for a pitcher.
   * Called when a pitcher name is clicked in PitcherTable.
   * @param {Object} pitcher - The full pitcher object from the table row
   */
  const handlePitcherClick = (pitcher) => {
    setModalPlayer(pitcher)
    setModalPlayerType('pitcher')
  }

  /**
   * Close the player detail modal.
   * Resets both modal state variables to null.
   */
  const handleModalClose = () => {
    setModalPlayer(null)
    setModalPlayerType(null)
  }

  // ---------------------------------------------------------------------------
  // COMPARISON HANDLERS
  // ---------------------------------------------------------------------------

  /**
   * Add a player to the comparison set. Merges computed stats and fantasy
   * points into the player object so the comparison table has all data.
   * Fantasy points are also merged at render time in PlayerComparison
   * (so league changes update the comparison without re-adding players),
   * but we merge here too for immediate availability.
   */
  const handleAddToComparison = (player, type) => {
    if (comparisonPlayers.length >= 5) return
    const playerId = player.id ?? player.player_id
    if (comparisonPlayers.some(p => (p.id ?? p.player_id) === playerId)) return
    if (comparisonType && comparisonType !== type) return

    // Merge computed stats + fantasy points
    let augmented = { ...player }
    if (type === 'batter') {
      const comp = computed.find(c => c.id === player.id)
      if (comp) augmented = { ...augmented, ...comp }
      const fp = fantasyBatterPts.find(f => f.id === player.id)
      if (fp) augmented = { ...augmented, fantasy_pts: fp.fantasy_pts, fantasy_pts_per_game: fp.fantasy_pts_per_game }
    } else {
      const comp = pitcherComputed.find(c => c.id === player.id)
      if (comp) augmented = { ...augmented, ...comp }
      const fp = fantasyPitcherPts.find(f => f.id === player.id)
      if (fp) augmented = { ...augmented, fantasy_pts: fp.fantasy_pts, fantasy_pts_per_game: fp.fantasy_pts_per_game }
    }

    setComparisonPlayers(prev => [...prev, augmented])
    setComparisonType(type)
    setComparisonOpen(true)
  }

  const handleRemoveFromComparison = (playerId) => {
    setComparisonPlayers(prev => {
      const updated = prev.filter(p => (p.id ?? p.player_id) !== playerId)
      if (updated.length === 0) setComparisonType(null)
      return updated
    })
  }

  const handleClearComparison = () => {
    setComparisonPlayers([])
    setComparisonType(null)
  }

  const handleToggleComparison = () => {
    setComparisonOpen(prev => !prev)
  }

  // ---------------------------------------------------------------------------
  // RENDER
  // ---------------------------------------------------------------------------

  // Show a loading message while the initial data fetch is in progress
  if (loading) {
    return <div className="app"><h1>MLB Player Stats</h1><p>Loading data...</p></div>
  }

  // ---------------------------------------------------------------------------
  // POSITION FILTERING
  // ---------------------------------------------------------------------------
  // Position filter groups for the dropdown
  const positionGroups = [
    { value: 'Batters', label: 'All Batters' },
    { value: 'Pitchers', label: 'All Pitchers' },
    { value: 'C', label: 'Catcher (C)' },
    { value: '1B', label: 'First Base (1B)' },
    { value: '2B', label: 'Second Base (2B)' },
    { value: '3B', label: 'Third Base (3B)' },
    { value: 'SS', label: 'Shortstop (SS)' },
    { value: '2B/SS', label: 'Middle Infield (2B/SS)' },
    { value: '1B/3B', label: 'Corner Infield (1B/3B)' },
    { value: 'OF', label: 'Outfield (LF/CF/RF)' },
    { value: 'DH', label: 'Designated Hitter (DH)' },
    { value: 'SP', label: 'Starting Pitcher (SP)' },
    { value: 'RP', label: 'Relief Pitcher (RP)' },
  ]

  // Filter players/pitchers based on position selection
  const filterByPosition = (data, filter) => {
    if (!data || data.length === 0) return []

    switch (filter) {
      case 'Batters':
        return data // All batters
      case 'Pitchers':
        return data // All pitchers (handled separately)
      case '2B/SS':
        return data.filter(p => p.position === '2B' || p.position === 'SS')
      case '1B/3B':
        return data.filter(p => p.position === '1B' || p.position === '3B')
      case 'OF':
        return data.filter(p => ['LF', 'CF', 'RF', 'OF'].includes(p.position))
      default:
        return data.filter(p => p.position === filter)
    }
  }

  // Determine if we're showing batters or pitchers
  const showPitchers = positionFilter === 'Pitchers' || positionFilter === 'SP' || positionFilter === 'RP'

  // Determine if we're currently showing rolling (time-period) stats.
  // When isRolling is true, we use rolling data arrays instead of season data,
  // and hide features that don't apply to rolling stats (edit, add player, search).
  const isRolling = activePeriod !== 'season'

  // Get the filtered data based on position, team, and rolling mode.
  // Filtering pipeline: base data → position filter → team filter → name search.
  //
  // The ?? (nullish coalescing) operator is key here:
  //   pitcherSearchResults ?? pitchers
  // means "use pitcherSearchResults if it's not null/undefined, otherwise use pitchers."
  // This lets search results seamlessly replace the full list when a search is active.
  const getFilteredData = () => {
    let data

    if (showPitchers) {
      // For pitchers: rolling data, search results, or all pitchers (in priority order)
      data = isRolling ? rollingPitchers : (pitcherSearchResults ?? pitchers)
      if (positionFilter !== 'Pitchers') data = filterByPosition(data, positionFilter)
    } else {
      // For batters: rolling data, search results, or all players (in priority order)
      data = isRolling ? rollingBatters : (searchResults ?? players)
      if (positionFilter !== 'Batters') data = filterByPosition(data, positionFilter)
    }

    // Team filter — narrows down to a specific team.
    // Applied AFTER position filtering so you can combine them:
    // e.g., "All Shortstops" + "Yankees" = only Yankees shortstops.
    if (teamFilter) {
      data = data.filter(p => p.team === teamFilter)
    }

    // Fuzzy name search — filters by player name with typo tolerance.
    // Applied last because it's a real-time text input that should
    // work on top of all other filters. Uses the fuzzyNameMatch()
    // utility function defined above the component.
    if (nameSearch.trim()) {
      data = data.filter(p => fuzzyNameMatch(nameSearch, p.name))
    }

    return data
  }

  const displayData = getFilteredData()

  // Build a sorted list of unique team names from BOTH players and pitchers.
  // This powers the "Team" dropdown in the header bar.
  // We combine both datasets so the dropdown has all teams regardless of
  // whether the user is viewing batters or pitchers.
  const allTeams = [...new Set([
    ...players.map(p => p.team),
    ...pitchers.map(p => p.team),
  ])].filter(Boolean).sort()

  // Dynamic table title based on position filter, search state, and rolling period.
  // Shows different prefixes depending on whether the user is viewing:
  //   - Rolling stats: "All Pitchers — Last 15 Days (42)"
  //   - Search results: "Search Results - All Pitchers (5 found)"
  //   - Normal view:    "All Pitchers (120)"
  const getTableTitle = () => {
    const group = positionGroups.find(g => g.value === positionFilter)
    const label = group ? group.label : positionFilter

    // Add the rolling period to the title so the user knows what they're viewing
    if (isRolling) {
      return `${label} — Last ${activePeriod} Days (${displayData.length})`
    }
    // Show "Search Results" prefix when a batter or pitcher search is active
    if (searchResults && !showPitchers) {
      return `Search Results - ${label} (${displayData.length} found)`
    }
    if (pitcherSearchResults && showPitchers) {
      return `Search Results - ${label} (${displayData.length} found)`
    }
    return `${label} (${displayData.length})`
  }

  const tableTitle = getTableTitle()

  // JSX below defines the component tree. Each child component receives
  // specific props (data) that it needs to render its part of the UI.
  return (
    <div className="app">
      <h1><a href="/" style={{ color: 'inherit', textDecoration: 'none' }}>MLB Player Stats</a></h1>

      {/* Show a visible error banner if the backend connection failed.
          This helps with debugging — without it, a backend failure would
          just show an empty page with no indication of what went wrong. */}
      {fetchError && (
        <div className="form-message form-message-error">
          {fetchError}
        </div>
      )}

      {/* StatsPanel shows league averages, team breakdowns, and computed stat explanations.
          Only visible in season mode — rolling stats don't have league averages. */}
      {!isRolling && <StatsPanel stats={stats} teamStats={teamStats} />}

      {/* PlayerForm is only relevant for full-season batter data.
          In rolling mode, the data comes from game logs (not the players table),
          so adding/editing doesn't apply. Hide it to avoid confusion. */}
      {!showPitchers && !isRolling && <PlayerForm onPlayerAdded={handlePlayerAdded} />}

      {/* PlayerSearch / PitcherSearch — always rendered (even in rolling mode)
          because the TimePeriodSelector now lives INSIDE these components.
          The stat filter inputs are hidden during rolling mode, but the
          time period buttons remain visible so the user can switch back.

          Additional props for the embedded TimePeriodSelector:
          - activePeriod: which time window is selected ('season', 5, 10, 15, 30)
          - onPeriodChange: callback when user clicks a period button
          - rollingLoading: disables period buttons while fetching rolling data
          - isRolling: true when viewing rolling stats (hides the stat filter inputs) */}
      {!showPitchers && (
        <PlayerSearch
          onSearchResults={handleSearchResults}
          onClearSearch={handleClearSearch}
          filterMeta={filterMeta}
          activePeriod={activePeriod}
          onPeriodChange={handlePeriodChange}
          rollingLoading={rollingLoading}
          isRolling={isRolling}
        />
      )}

      {showPitchers && (
        <PitcherSearch
          onSearchResults={handlePitcherSearchResults}
          onClearSearch={handleClearPitcherSearch}
          filterMeta={pitcherFilterMeta}
          activePeriod={activePeriod}
          onPeriodChange={handlePeriodChange}
          rollingLoading={rollingLoading}
          isRolling={isRolling}
        />
      )}

      {/* -----------------------------------------------------------------------
          TABLE HEADER BAR
          -----------------------------------------------------------------------
          A unified header sitting directly above the data table, containing:
          1. Table title with count (e.g., "All Batters (150)")
          2. Position filter dropdown (Batters, Pitchers, C, SS, etc.)
          3. Team filter dropdown (All Teams, Yankees, Dodgers, etc.)
          4. Fuzzy name search input (type to filter by player name)

          The position and team filters COMBINE — selecting "Shortstop" + "Yankees"
          shows only Yankees shortstops. The name search applies on top of both.

          We render this in App.jsx (not inside the table components) so there's
          one unified header bar regardless of which table is showing. */}
      <div className="table-header-bar">
        <h2 className="table-header-title">{tableTitle}</h2>
        <div className="table-header-filters">
          {/* Position filter — switches between Batters/Pitchers and specific positions */}
          <div className="table-header-filter">
            <label htmlFor="position-select">View:</label>
            <select
              id="position-select"
              value={positionFilter}
              onChange={(e) => setPositionFilter(e.target.value)}
            >
              {positionGroups.map(group => (
                <option key={group.value} value={group.value}>
                  {group.label}
                </option>
              ))}
            </select>
          </div>

          {/* Team filter — narrows results to a specific team.
              Works alongside the position filter for combined filtering.
              e.g., "Shortstop" + "Yankees" = only Yankees shortstops. */}
          <div className="table-header-filter">
            <label htmlFor="team-select">Team:</label>
            <select
              id="team-select"
              value={teamFilter}
              onChange={(e) => setTeamFilter(e.target.value)}
            >
              <option value="">All Teams</option>
              {allTeams.map(t => (
                <option key={t} value={t}>{t}</option>
              ))}
            </select>
          </div>

          {/* Fantasy League selector — choose which ESPN league's scoring to apply.
              Sits alongside Position and Team dropdowns so all filters are grouped.
              When a league is selected, a "Fantasy Pts" column appears in the tables.
              The "+ League" button opens an inline form to connect a new ESPN league. */}
          <LeagueSelector
            leagues={fantasyLeagues}
            activeLeagueId={activeLeagueId}
            onLeagueChange={handleLeagueChange}
            onLeagueAdded={handleLeagueAdded}
            onLeagueRemoved={handleLeagueRemoved}
          />
        </div>
      </div>

      {/* Fuzzy name search — a text input placed between the header bar and
          the table. As the user types, the table filters in real-time using
          Levenshtein distance for typo tolerance. No "Search" button needed —
          filtering happens on every keystroke.

          Examples of fuzzy matching:
          - "judge" → matches "Aaron Judge" (substring)
          - "juge"  → matches "Aaron Judge" (1 edit away)
          - "otani" → matches "Shohei Ohtani" (1 edit away) */}
      <div className="name-search-bar">
        <input
          type="text"
          placeholder="Search by player name..."
          value={nameSearch}
          onChange={(e) => setNameSearch(e.target.value)}
        />
        {/* Show a clear button when text is entered, letting users
            quickly reset the name search with one click */}
        {nameSearch && (
          <button
            className="name-search-clear"
            onClick={() => setNameSearch('')}
            title="Clear name search"
          >
            ✕
          </button>
        )}
      </div>

      {/* Player Comparison Panel — collapsible panel for side-by-side stat comparison.
          Players can be added via the autocomplete search or the Compare buttons in table rows. */}
      <PlayerComparison
        comparisonPlayers={comparisonPlayers}
        comparisonType={comparisonType}
        isOpen={comparisonOpen}
        onToggle={handleToggleComparison}
        onRemovePlayer={handleRemoveFromComparison}
        onClearAll={handleClearComparison}
        onAddPlayer={handleAddToComparison}
        onPlayerClick={(player, type) => {
          if (type === 'pitcher') handlePitcherClick(player)
          else handlePlayerClick(player)
        }}
        allBatters={players}
        allPitchers={pitchers}
        computed={computed}
        pitcherComputed={pitcherComputed}
        fantasyBatterPts={fantasyBatterPts}
        fantasyPitcherPts={fantasyPitcherPts}
      />

      {/* Conditionally render PlayerTable or PitcherTable based on position filter.
          In rolling mode, we pass isRolling=true so the table can hide the
          edit/actions column (rolling data isn't editable — it's aggregated). */}
      {showPitchers ? (
        <PitcherTable
          pitchers={displayData}
          computed={isRolling ? [] : pitcherComputed}
          fantasyPoints={fantasyPitcherPts}
          onPitcherUpdated={handlePitcherUpdated}
          isRolling={isRolling}
          onPitcherClick={handlePitcherClick}
          comparisonIds={comparisonIds}
          onAddToComparison={handleAddToComparison}
        />
      ) : (
        <PlayerTable
          players={displayData}
          computed={isRolling ? [] : computed}
          fantasyPoints={fantasyBatterPts}
          onPlayerUpdated={handlePlayerUpdated}
          isRolling={isRolling}
          onPlayerClick={handlePlayerClick}
          comparisonIds={comparisonIds}
          onAddToComparison={handleAddToComparison}
        />
      )}

      {/* Player Detail Modal — shown when a player/pitcher name is clicked.
          Displays headshot, ESPN news articles, and MLB transaction history. */}
      {modalPlayer && (
        <PlayerModal
          player={modalPlayer}
          playerType={modalPlayerType}
          onClose={handleModalClose}
        />
      )}
    </div>
  )
}

export default App
