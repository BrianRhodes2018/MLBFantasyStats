/**
 * PlayerSearch.jsx - Dynamic Player Search/Filter Component
 * ===========================================================
 *
 * A search panel that lets users filter players by position, team, and any
 * combination of stat ranges. The filter inputs are built DYNAMICALLY from
 * the backend's /players/filterable-stats endpoint, which means:
 *   - If you add a new stat column to models.py, a new filter row appears
 *     in the UI automatically — no frontend code changes needed.
 *
 * Key React concepts demonstrated:
 * - Dynamic form generation: Building inputs from API data instead of hardcoding
 * - URLSearchParams: The Web API for constructing query strings
 * - Conditional logic in event handlers: Only include non-empty filters
 * - useEffect with dependency: Re-fetch filter metadata when data changes
 * - Controlled select (dropdown): Same pattern as controlled inputs
 *
 * Key API concepts:
 * - Query parameters: /players/search?position=RF&min_home_runs=30&max_ops=1.0
 * - The response includes { code, message, data } — Swagger-style format
 * - The "message" field describes what filters matched and how many results
 *
 * Props:
 * @param {Function} onSearchResults - Called with the search response data
 *   when results come back from the API. Parent uses this to update the table.
 * @param {Function} onClearSearch - Called when user clicks "Clear" to reset
 *   the table back to showing all players.
 * @param {Object} filterMeta - Metadata from GET /players/filterable-stats:
 *   { stats: [{name, type, min, max, avg}, ...], positions: [...], teams: [...] }
 * @param {string|number} activePeriod - Currently selected time period ('season', 5, 10, 15, 30)
 * @param {Function} onPeriodChange - Callback when a time period button is clicked
 * @param {boolean} rollingLoading - True while rolling stats are being fetched
 * @param {boolean} isRolling - True when viewing rolling stats (hides stat filter inputs)
 */

import { useState } from 'react'
import TimePeriodSelector from './TimePeriodSelector'
import { API_BASE } from '../config'

/**
 * Column order matching PlayerTable — filter rows appear in this same order.
 * This keeps the search panel visually aligned with the results table,
 * so it's easy to match a filter field to its corresponding table column.
 *
 * If a new stat is added to the backend but not listed here, it will
 * appear at the end of the filter list (future-proofing).
 */
const STAT_ORDER = [
  'games', 'at_bats', 'batting_average', 'home_runs', 'rbi',
  'stolen_bases', 'runs', 'strikeouts', 'total_bases', 'obp', 'ops'
]

/**
 * All standard MLB fielding positions.
 *
 * We define these statically rather than relying solely on the backend data
 * because the backend's /players/filterable-stats endpoint only returns
 * positions that EXIST in the database. If all current players have
 * position: null (e.g., legacy data from before the position column was added),
 * the dropdown would be empty and unusable.
 *
 * By providing a static list AND merging with backend data, we ensure:
 * 1. The dropdown always has all standard positions available
 * 2. Any custom/non-standard positions in the DB are also included
 *
 * Position abbreviations:
 *   C = Catcher, 1B = First Base, 2B = Second Base, 3B = Third Base,
 *   SS = Shortstop, LF = Left Field, CF = Center Field, RF = Right Field,
 *   DH = Designated Hitter, SP = Starting Pitcher, RP = Relief Pitcher
 */
const ALL_POSITIONS = ['C', '1B', '2B', '3B', 'SS', 'LF', 'CF', 'RF', 'DH', 'SP', 'RP']

/**
 * Format a stat column name for display.
 * Converts snake_case database names to Title Case labels.
 * e.g., "batting_average" -> "Batting Average", "home_runs" -> "Home Runs"
 *
 * How it works:
 * 1. .split("_") turns "batting_average" into ["batting", "average"]
 * 2. .map() capitalizes the first letter of each word
 * 3. .join(" ") puts them back together with spaces
 *
 * @param {string} name - The snake_case stat name from the database
 * @returns {string} The formatted display label
 */
const formatStatName = (name) => {
  return name
    .split('_')
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ')
}

function PlayerSearch({ onSearchResults, onClearSearch, filterMeta, activePeriod, onPeriodChange, rollingLoading, isRolling, season }) {
  // State for the position and team dropdown selections
  const [position, setPosition] = useState('')
  const [team, setTeam] = useState('')

  // State for stat range filters — an object keyed by stat name.
  // Each stat has a { min: '', max: '' } pair.
  // Example: { batting_average: { min: '0.280', max: '' }, home_runs: { min: '', max: '50' } }
  //
  // We initialize this as an empty object. When filterMeta loads, the inputs
  // are rendered dynamically and their onChange handlers update this object.
  const [statFilters, setStatFilters] = useState({})

  // State for the API response message (shown below the search button)
  const [searchMessage, setSearchMessage] = useState(null)

  /**
   * Helper: compute half the league average for a stat, formatted by type.
   *
   * Used in the min input's placeholder text so users see a suggested
   * starting threshold without auto-applying it. This avoids the problem
   * of pre-filled values filtering too aggressively (e.g., relief pitchers
   * getting excluded because they naturally have lower counting stats).
   *
   * @param {Object} stat - Stat metadata object { name, type, min, max, avg }
   * @returns {string} Formatted half-average value, or the min as fallback
   */
  const getHalfAvgLabel = (stat) => {
    if (stat.avg == null) return stat.min
    const halfAvg = stat.avg / 2
    return stat.type === 'float' ? halfAvg.toFixed(3) : Math.round(halfAvg)
  }

  /**
   * Sort filter stats to match the table column order defined in STAT_ORDER.
   *
   * Array.prototype.sort() compares items pairwise — if the return value is
   * negative, `a` comes first; positive means `b` first; zero means equal.
   *
   * indexOf() returns -1 for stats not in the order array. We map -1 to 999
   * so unknown stats sort to the end rather than the beginning.
   *
   * We spread into a new array ([...]) to avoid mutating filterMeta.stats.
   */
  const sortedStats = filterMeta?.stats
    ? [...filterMeta.stats].sort((a, b) => {
        const indexA = STAT_ORDER.indexOf(a.name)
        const indexB = STAT_ORDER.indexOf(b.name)
        return (indexA === -1 ? 999 : indexA) - (indexB === -1 ? 999 : indexB)
      })
    : []

  /**
   * Handle changes to a stat range input (min or max for a specific stat).
   *
   * This uses a nested state update pattern:
   * 1. Spread the existing statFilters to keep other stats unchanged
   * 2. For the specific stat being changed, spread its existing min/max
   * 3. Override just the one field (min or max) that changed
   *
   * @param {string} statName - Which stat (e.g., "home_runs")
   * @param {string} bound - Which bound ("min" or "max")
   * @param {string} value - The new value from the input
   */
  const handleStatFilterChange = (statName, bound, value) => {
    setStatFilters((prev) => ({
      ...prev,
      [statName]: {
        ...(prev[statName] || {}),  // Keep existing min/max for this stat
        [bound]: value,              // Update just the min or max
      },
    }))
  }

  /**
   * Execute the search by building a query string and calling the API.
   *
   * URLSearchParams is a built-in Web API for constructing URL query strings.
   * It handles encoding special characters and joining key=value pairs with &.
   *
   * Example output: "position=RF&min_home_runs=30&max_ops=1.0"
   *
   * We only append parameters that have non-empty values. This keeps the URL
   * clean and avoids sending empty filters to the backend.
   */
  const handleSearch = async () => {
    // URLSearchParams builds query strings: new URLSearchParams() starts empty,
    // .append(key, value) adds entries, .toString() produces "key1=val1&key2=val2"
    const params = new URLSearchParams()

    // Only add team filter if user selected one (not the "All Teams" default)
    if (team) params.append('team', team)

    // Only add position filter if user selected one
    if (position) params.append('position', position)

    // Add stat range filters — only include non-empty values
    // Object.entries() converts { home_runs: { min: "30", max: "" } }
    // into [["home_runs", { min: "30", max: "" }]] for iteration.
    Object.entries(statFilters).forEach(([statName, bounds]) => {
      if (bounds.min !== undefined && bounds.min !== '') {
        params.append(`min_${statName}`, bounds.min)
      }
      if (bounds.max !== undefined && bounds.max !== '') {
        params.append(`max_${statName}`, bounds.max)
      }
    })

    // Build the full URL with query string
    // params.toString() produces something like "position=RF&min_home_runs=30"
    // Include season param for historical snapshot queries
    if (season) params.append('season', season)
    const queryString = params.toString()
    const url = queryString ? `${API_BASE}/players/search?${queryString}` : `${API_BASE}/players/search`

    try {
      const res = await fetch(url)
      const data = await res.json()

      // The API returns ApiResponse: { code: 200, message: "Found 3 player(s)...", data: {...} }
      // Display the code and message to the user, then pass results to parent.
      setSearchMessage({ text: `[${data.code}] ${data.message}`, type: 'success' })
      onSearchResults(data.data)
    } catch (error) {
      setSearchMessage({ text: `[Error] ${error.message}`, type: 'error' })
    }
  }

  /**
   * Clear all filters and reset to showing all players.
   * Resets every piece of local state back to its initial value.
   */
  const handleClear = () => {
    setPosition('')
    setTeam('')
    setStatFilters({})
    setSearchMessage(null)
    onClearSearch()
  }

  return (
    <div className="player-search">
      <h2>Search Players</h2>

      {/* TimePeriodSelector — row of buttons to toggle between Season / Last N Days.
          Placed inside the search panel so the timeframe toggle and stat filters
          are visually grouped together in one card. The activePeriod state controls
          which button is highlighted. rollingLoading disables buttons while data
          is being fetched.

          This is ALWAYS visible (even during rolling mode) so the user can
          switch back to season view. The stat filters below are hidden during
          rolling mode since rolling data doesn't support search/filtering. */}
      <TimePeriodSelector
        activePeriod={activePeriod}
        onPeriodChange={onPeriodChange}
        loading={rollingLoading}
      />

      {/* Stat filter inputs are only shown in season mode.
          Rolling stats come from game log aggregations and don't support
          the same search/filter API, so we hide the filters to avoid confusion.

          We also guard against filterMeta being null (still loading from backend)
          — show a loading message until the filter metadata arrives. */}
      {!isRolling && (
        !filterMeta ? (
          <p>Loading search filters...</p>
        ) : (
          <>
            {/* Response message from the search API — shows code and message like Swagger */}
            {searchMessage && (
              <div className={`form-message form-message-${searchMessage.type}`}>
                {searchMessage.text}
              </div>
            )}

            <form className="search-filters" onSubmit={(e) => { e.preventDefault(); handleSearch() }}>
              <div className="search-dropdowns">
                <div className="filter-group">
                  <label>Position</label>
                  <select value={position} onChange={(e) => setPosition(e.target.value)}>
                    <option value="">All Positions</option>
                    {[...new Set([...ALL_POSITIONS, ...filterMeta.positions])].sort().map((pos) => (
                      <option key={pos} value={pos}>{pos}</option>
                    ))}
                  </select>
                </div>

                <div className="filter-group">
                  <label>Team</label>
                  <select value={team} onChange={(e) => setTeam(e.target.value)}>
                    <option value="">All Teams</option>
                    {filterMeta.teams.map((t) => (
                      <option key={t} value={t}>{t}</option>
                    ))}
                  </select>
                </div>
              </div>

              {/* Dynamic stat range filters — sorted to match the table column order.
                  Each row has a "Min" and "Max" input. Min is pre-filled with half
                  the league average (from useEffect above), giving users a one-click
                  starting point for finding above-average players. */}
              <div className="search-stat-filters">
                {sortedStats.map((stat) => (
                  <div className="filter-group" key={stat.name}>
                    <label>{formatStatName(stat.name)}</label>
                    <div className="min-max-inputs">
                      {/* Min placeholder shows half the league average as a suggestion.
                          Users see a useful reference value without auto-filtering.
                          e.g., "Min (10)" for a stat whose average is 20. */}
                      <input
                        type="number"
                        step={stat.type === 'float' ? '0.001' : '1'}
                        placeholder={`Min (${getHalfAvgLabel(stat)})`}
                        value={statFilters[stat.name]?.min || ''}
                        onChange={(e) => handleStatFilterChange(stat.name, 'min', e.target.value)}
                      />
                      <span className="range-separator">to</span>
                      <input
                        type="number"
                        step={stat.type === 'float' ? '0.001' : '1'}
                        placeholder={`Max`}
                        value={statFilters[stat.name]?.max || ''}
                        onChange={(e) => handleStatFilterChange(stat.name, 'max', e.target.value)}
                      />
                    </div>
                  </div>
                ))}
              </div>

              <div className="search-actions">
                <button type="submit" className="btn-search">Search</button>
                <button type="button" onClick={handleClear} className="btn-clear">Clear</button>
              </div>
            </form>
          </>
        )
      )}
    </div>
  )
}

export default PlayerSearch
