/**
 * PitcherSearch.jsx - Dynamic Pitcher Search/Filter Component
 * =============================================================
 *
 * A search panel that lets users filter pitchers by position (SP/RP), team,
 * and any combination of stat ranges. Mirrors the PlayerSearch component
 * but targets the /pitchers/search and /pitchers/filterable-stats endpoints.
 *
 * The filter inputs are built DYNAMICALLY from the backend's
 * /pitchers/filterable-stats endpoint — adding a new stat column to
 * the pitchers table in models.py automatically creates a new filter row
 * in the UI without any frontend code changes.
 *
 * Key React concepts demonstrated:
 * - Dynamic form generation from API metadata
 * - URLSearchParams for building query strings
 * - Controlled inputs with stateful min/max pairs
 * - Form submission via onSubmit (Enter key triggers search)
 *
 * Props:
 * @param {Function} onSearchResults - Called with search response data
 *   when results come back. Parent uses this to update the pitcher table.
 * @param {Function} onClearSearch - Called when user clicks "Clear" to reset
 *   the table back to showing all pitchers.
 * @param {Object} filterMeta - Metadata from GET /pitchers/filterable-stats:
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
 * Column order matching PitcherTable — filter rows appear in this same order.
 * This keeps the search panel visually aligned with the results table,
 * so it's easy to match a filter field to its corresponding table column.
 *
 * If a new stat is added to the backend but not listed here, it will
 * appear at the end of the filter list (future-proofing).
 */
const STAT_ORDER = [
  'games', 'wins', 'losses', 'era', 'whip', 'innings_pitched',
  'hits_allowed', 'earned_runs', 'walks', 'strikeouts',
  'home_runs_allowed', 'saves', 'quality_starts',
  'k_per_9', 'bb_per_9', 'k_bb_ratio', 'hr_per_9'
]

/**
 * Pitcher positions — simpler than batters since there are only two.
 * SP = Starting Pitcher, RP = Relief Pitcher.
 *
 * We provide a static fallback in case the backend hasn't loaded yet
 * or the database is empty.
 */
const ALL_POSITIONS = ['SP', 'RP']

/**
 * Format a stat column name for display.
 * Converts snake_case database names to Title Case labels.
 * e.g., "earned_runs" -> "Earned Runs", "innings_pitched" -> "Innings Pitched"
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

function PitcherSearch({ onSearchResults, onClearSearch, filterMeta, activePeriod, onPeriodChange, rollingLoading, isRolling, season }) {
  // State for the position and team dropdown selections.
  // '' means "no filter" (show all).
  const [position, setPosition] = useState('')
  const [team, setTeam] = useState('')

  // State for stat range filters — an object keyed by stat name.
  // Each stat has a { min: '', max: '' } pair.
  // Example: { era: { min: '', max: '3.50' }, strikeouts: { min: '150', max: '' } }
  const [statFilters, setStatFilters] = useState({})

  // State for the API response message (e.g., "[200] Found 5 pitcher(s)...")
  const [searchMessage, setSearchMessage] = useState(null)

  /**
   * Helper: compute half the league average for a stat, formatted by type.
   *
   * Used in the min input's placeholder text so users see a suggested
   * starting threshold without auto-applying it. Keeping these as
   * placeholders (not actual values) avoids the problem of pre-filled
   * values filtering too aggressively — relief pitchers have vastly
   * different counting stats than starters, so applying minimums
   * for ALL stats simultaneously would exclude most RPs.
   *
   * @param {Object} stat - Stat metadata object { name, type, min, max, avg }
   * @returns {string} Formatted half-average value, or the min as fallback
   */
  const getHalfAvgLabel = (stat) => {
    if (stat.avg == null) return stat.min
    const halfAvg = stat.avg / 2
    return stat.type === 'float' ? halfAvg.toFixed(2) : Math.round(halfAvg)
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
   * Uses nested state update pattern:
   * 1. Spread existing statFilters to keep other stats unchanged
   * 2. For the specific stat, spread its existing min/max
   * 3. Override just the one bound (min or max) that changed
   *
   * @param {string} statName - Which stat (e.g., "era")
   * @param {string} bound - Which bound ("min" or "max")
   * @param {string} value - The new value from the input
   */
  const handleStatFilterChange = (statName, bound, value) => {
    setStatFilters((prev) => ({
      ...prev,
      [statName]: {
        ...(prev[statName] || {}),
        [bound]: value,
      },
    }))
  }

  /**
   * Execute the search by building a query string and calling the API.
   *
   * Builds a URL like: /pitchers/search?position=SP&max_era=3.50&min_strikeouts=150
   * Only includes parameters that have non-empty values.
   */
  const handleSearch = async () => {
    const params = new URLSearchParams()

    // Only add filters that the user has actually set
    if (team) params.append('team', team)
    if (position) params.append('position', position)

    // Add stat range filters — only include non-empty min/max values.
    // Object.entries() converts the statFilters object into an iterable array
    // of [key, value] pairs for looping.
    Object.entries(statFilters).forEach(([statName, bounds]) => {
      if (bounds.min !== undefined && bounds.min !== '') {
        params.append(`min_${statName}`, bounds.min)
      }
      if (bounds.max !== undefined && bounds.max !== '') {
        params.append(`max_${statName}`, bounds.max)
      }
    })

    // Include season param for historical snapshot queries
    if (season) params.append('season', season)
    // Build the full URL. If no filters, still call search (returns all pitchers).
    const queryString = params.toString()
    const url = queryString ? `${API_BASE}/pitchers/search?${queryString}` : `${API_BASE}/pitchers/search`

    try {
      const res = await fetch(url)
      const data = await res.json()

      // Show the API response code + message, then pass results to parent
      setSearchMessage({ text: `[${data.code}] ${data.message}`, type: 'success' })
      onSearchResults(data.data)
    } catch (error) {
      setSearchMessage({ text: `[Error] ${error.message}`, type: 'error' })
    }
  }

  /**
   * Clear all filters and reset to showing all pitchers.
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
      <h2>Search Pitchers</h2>

      {/* TimePeriodSelector — row of buttons to toggle between Season / Last N Days.
          Placed inside the search panel so the timeframe toggle and stat filters
          are visually grouped together in one card. Always visible so the user
          can switch back to season view even while in rolling mode. */}
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
            {/* Response message from the search API */}
            {searchMessage && (
              <div className={`form-message form-message-${searchMessage.type}`}>
                {searchMessage.text}
              </div>
            )}

            {/* Wrapping in a <form> with onSubmit lets Enter key trigger search.
                e.preventDefault() stops the browser from reloading the page
                (the default form submission behavior). */}
            <form className="search-filters" onSubmit={(e) => { e.preventDefault(); handleSearch() }}>
              {/* Dropdown filters for position (SP/RP) and team */}
              <div className="search-dropdowns">
                <div className="filter-group">
                  <label>Position</label>
                  <select value={position} onChange={(e) => setPosition(e.target.value)}>
                    <option value="">All Positions</option>
                    {/* Merge static positions with any from the database.
                        new Set() removes duplicates, spread into array for .sort().map() */}
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
                  starting point for finding above-average pitchers. */}
              <div className="search-stat-filters">
                {sortedStats.map((stat) => (
                  <div className="filter-group" key={stat.name}>
                    <label>{formatStatName(stat.name)}</label>
                    <div className="min-max-inputs">
                      {/* Min placeholder shows half the league average as a suggestion.
                          Users see a useful reference value without auto-filtering. */}
                      <input
                        type="number"
                        step={stat.type === 'float' ? '0.01' : '1'}
                        placeholder={`Min (${getHalfAvgLabel(stat)})`}
                        value={statFilters[stat.name]?.min || ''}
                        onChange={(e) => handleStatFilterChange(stat.name, 'min', e.target.value)}
                      />
                      <span className="range-separator">to</span>
                      <input
                        type="number"
                        step={stat.type === 'float' ? '0.01' : '1'}
                        placeholder={`Max`}
                        value={statFilters[stat.name]?.max || ''}
                        onChange={(e) => handleStatFilterChange(stat.name, 'max', e.target.value)}
                      />
                    </div>
                  </div>
                ))}
              </div>

              {/* Search and Clear buttons.
                  Search is type="submit" so Enter key works.
                  Clear is type="button" so it doesn't trigger form submission. */}
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

export default PitcherSearch
