/**
 * PlayerComparison.jsx — Side-by-Side Player Comparison Panel
 * ============================================================
 *
 * A collapsible panel that lets users compare up to 5 players side-by-side.
 * Players can be added via:
 *   1. Autocomplete search input (fuzzy matching against all loaded players)
 *   2. "Compare" buttons on table rows (handled by parent via onAddPlayer)
 *
 * The comparison uses a standard table layout matching the main player chart:
 * stats as column headers, each player as a row. The best value in each stat
 * column is highlighted with accent color.
 *
 * Batters and pitchers are compared separately (type locks on first add).
 */

import { useState, useRef, useEffect } from 'react'
import { fuzzyNameMatch } from '../utils/fuzzyMatch'

// ---------------------------------------------------------------------------
// STAT DEFINITIONS — which stats to show for each player type
// ---------------------------------------------------------------------------

// Batter comparison stats — matches PlayerTable column order exactly.
// The formatRow property receives the full player object for composite display (H/AB).
// The isFantasy flag hides the column when no fantasy league is selected.
const BATTER_COMPARE_STATS = [
  { key: 'team', label: 'Team' },
  { key: 'position', label: 'Pos', format: (v) => v ?? '—' },
  {
    key: 'at_bats', label: 'H/AB', numeric: true,
    formatRow: (player) => {
      const h = player.hits ?? '—'
      const ab = player.at_bats ?? '—'
      return `${h}/${ab}`
    },
  },
  { key: 'batting_average', label: 'AVG', format: (v) => v?.toFixed(3), numeric: true },
  { key: 'home_runs', label: 'HR', numeric: true },
  { key: 'rbi', label: 'RBI', numeric: true },
  { key: 'stolen_bases', label: 'SB', numeric: true },
  { key: 'runs', label: 'R', numeric: true },
  { key: 'strikeouts', label: 'K', numeric: true, lowerIsBetter: true },
  { key: 'total_bases', label: 'TB', numeric: true },
  { key: 'obp', label: 'OBP', format: (v) => v?.toFixed(3), numeric: true },
  { key: 'ops', label: 'OPS', format: (v) => v?.toFixed(3), numeric: true },
  { key: 'power_index', label: 'Power Idx', format: (v) => v != null ? v.toFixed(1) : '—', numeric: true },
  { key: 'speed_score', label: 'Speed', format: (v) => v != null ? v.toFixed(1) : '—', numeric: true },
  { key: 'fantasy_pts', label: 'Fantasy Pts', format: (v) => v != null ? v.toFixed(1) : '—', numeric: true, isFantasy: true },
  { key: 'fantasy_pts_per_game', label: 'Pts/G', format: (v) => v != null ? v.toFixed(1) : '—', numeric: true, isFantasy: true },
]

// Pitcher comparison stats — matches PitcherTable column order exactly.
const PITCHER_COMPARE_STATS = [
  { key: 'team', label: 'Team' },
  { key: 'position', label: 'Pos', format: (v) => v ?? '—' },
  { key: 'wins', label: 'W', numeric: true },
  { key: 'losses', label: 'L', numeric: true, lowerIsBetter: true },
  { key: 'era', label: 'ERA', format: (v) => v?.toFixed(2), numeric: true, lowerIsBetter: true },
  { key: 'whip', label: 'WHIP', format: (v) => v?.toFixed(2), numeric: true, lowerIsBetter: true },
  { key: 'innings_pitched', label: 'IP', format: (v) => v?.toFixed(1), numeric: true },
  { key: 'hits_allowed', label: 'H', numeric: true, lowerIsBetter: true },
  { key: 'earned_runs', label: 'ER', numeric: true, lowerIsBetter: true },
  { key: 'walks', label: 'BB', numeric: true, lowerIsBetter: true },
  { key: 'strikeouts', label: 'K', numeric: true },
  { key: 'home_runs_allowed', label: 'HR', numeric: true, lowerIsBetter: true },
  { key: 'saves', label: 'SV', numeric: true },
  { key: 'quality_starts', label: 'QS', format: (v) => v ?? '—', numeric: true },
  { key: 'k_per_9', label: 'K/9', format: (v) => v != null ? v.toFixed(1) : '—', numeric: true },
  { key: 'bb_per_9', label: 'BB/9', format: (v) => v != null ? v.toFixed(1) : '—', numeric: true, lowerIsBetter: true },
  { key: 'k_bb_ratio', label: 'K/BB', format: (v) => v != null ? v.toFixed(2) : '—', numeric: true },
  { key: 'hr_per_9', label: 'HR/9', format: (v) => v != null ? v.toFixed(2) : '—', numeric: true, lowerIsBetter: true },
  { key: 'fantasy_pts', label: 'Fantasy Pts', format: (v) => v != null ? v.toFixed(1) : '—', numeric: true, isFantasy: true },
  { key: 'fantasy_pts_per_game', label: 'Pts/G', format: (v) => v != null ? v.toFixed(1) : '—', numeric: true, isFantasy: true },
]

// ---------------------------------------------------------------------------
// COMPONENT
// ---------------------------------------------------------------------------

function PlayerComparison({
  comparisonPlayers,
  comparisonType,
  isOpen,
  onToggle,
  onRemovePlayer,
  onClearAll,
  onAddPlayer,
  allBatters,
  allPitchers,
  computed,
  pitcherComputed,
  fantasyBatterPts,
  fantasyPitcherPts,
}) {
  const [searchQuery, setSearchQuery] = useState('')
  const [suggestions, setSuggestions] = useState([])
  const [showSuggestions, setShowSuggestions] = useState(false)
  const searchRef = useRef(null)

  // Close suggestions when clicking outside
  useEffect(() => {
    const handleClickOutside = (e) => {
      if (searchRef.current && !searchRef.current.contains(e.target)) {
        setShowSuggestions(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  // Update suggestions when search query changes
  useEffect(() => {
    if (!searchQuery.trim()) {
      setSuggestions([])
      return
    }

    const results = []
    const existingIds = new Set(comparisonPlayers.map(p => p.id ?? p.player_id))

    // Search batters (if type is null or 'batter')
    if (!comparisonType || comparisonType === 'batter') {
      for (const p of allBatters) {
        if (existingIds.has(p.id)) continue
        if (fuzzyNameMatch(searchQuery, p.name)) {
          // Merge computed stats
          const comp = computed?.find(c => c.id === p.id)
          results.push({ ...p, ...(comp || {}), _type: 'batter' })
        }
        if (results.length >= 8) break
      }
    }

    // Search pitchers (if type is null or 'pitcher')
    if ((!comparisonType || comparisonType === 'pitcher') && results.length < 8) {
      for (const p of allPitchers) {
        if (existingIds.has(p.id)) continue
        if (fuzzyNameMatch(searchQuery, p.name)) {
          const comp = pitcherComputed?.find(c => c.id === p.id)
          results.push({ ...p, ...(comp || {}), _type: 'pitcher' })
        }
        if (results.length >= 8) break
      }
    }

    setSuggestions(results)
    setShowSuggestions(results.length > 0)
  }, [searchQuery, comparisonType, comparisonPlayers, allBatters, allPitchers, computed, pitcherComputed])

  /**
   * Handle selecting a suggestion from the autocomplete dropdown.
   */
  const handleSelectSuggestion = (player) => {
    const type = player._type
    const { _type, ...cleanPlayer } = player  // Remove the _type tag
    onAddPlayer(cleanPlayer, type)
    setSearchQuery('')
    setShowSuggestions(false)
  }

  // Determine if fantasy data is available (a league is selected and points exist)
  const fantasyPts = comparisonType === 'pitcher' ? fantasyPitcherPts : fantasyBatterPts
  const hasFantasy = fantasyPts && fantasyPts.length > 0

  // Pick the right stat definitions and filter out fantasy columns when no league is selected
  const allStats = comparisonType === 'pitcher' ? PITCHER_COMPARE_STATS : BATTER_COMPARE_STATS
  const stats = allStats.filter(s => !s.isFantasy || hasFantasy)

  // Merge fantasy points into comparison players at render time so that
  // changing leagues immediately updates the comparison table without needing
  // to re-add players. This mirrors how PlayerTable/PitcherTable merge
  // fantasy data on every render in their mergedPlayers step.
  const displayPlayers = comparisonPlayers.map((player) => {
    if (!hasFantasy) return player
    const fp = fantasyPts.find(f => f.id === (player.id ?? player.player_id))
    if (!fp) return player
    return { ...player, fantasy_pts: fp.fantasy_pts, fantasy_pts_per_game: fp.fantasy_pts_per_game }
  })

  /**
   * Find the best value for a given stat across all comparison players.
   * Returns the best raw value, or null if comparison isn't applicable.
   * Uses displayPlayers (with fantasy data merged) for accurate comparison.
   */
  const getBestValue = (statDef) => {
    if (!statDef.numeric || displayPlayers.length < 2) return null
    const vals = displayPlayers.map(p => p[statDef.key]).filter(v => v != null && !isNaN(v))
    if (vals.length === 0) return null
    return statDef.lowerIsBetter ? Math.min(...vals) : Math.max(...vals)
  }

  return (
    <div className="comparison-panel">
      {/* Collapsible header bar */}
      <div className="comparison-header" onClick={onToggle}>
        <div className="comparison-header-left">
          <span className="collapse-indicator">{isOpen ? '▾' : '▸'}</span>
          <h2 className="comparison-title">
            Player Comparison
            {comparisonPlayers.length > 0 && (
              <span className="comparison-count"> ({comparisonPlayers.length})</span>
            )}
          </h2>
        </div>
        {comparisonPlayers.length > 0 && (
          <button
            className="btn-clear-compare"
            onClick={(e) => { e.stopPropagation(); onClearAll(); }}
            title="Clear all players from comparison"
          >
            Clear All
          </button>
        )}
      </div>

      {/* Expanded body */}
      {isOpen && (
        <div className="comparison-body">
          {/* Autocomplete search input */}
          {comparisonPlayers.length < 5 && (
            <div className="comparison-search" ref={searchRef}>
              <input
                type="text"
                placeholder={
                  comparisonType
                    ? `Search ${comparisonType}s to compare...`
                    : 'Search players to compare...'
                }
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                onFocus={() => suggestions.length > 0 && setShowSuggestions(true)}
              />
              {showSuggestions && (
                <div className="comparison-suggestions">
                  {suggestions.map((player, idx) => (
                    <div
                      key={player.id ?? idx}
                      className="comparison-suggestion"
                      onClick={() => handleSelectSuggestion(player)}
                    >
                      <span className="suggestion-name">{player.name}</span>
                      <span className="suggestion-meta">
                        <span className="suggestion-team">{player.team}</span>
                        {!comparisonType && (
                          <span className="suggestion-type">{player._type}</span>
                        )}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
          {comparisonPlayers.length >= 5 && (
            <div className="comparison-cap">Maximum of 5 players reached</div>
          )}

          {/* Empty state */}
          {comparisonPlayers.length === 0 && (
            <div className="comparison-empty">
              Search for a player above or click "Compare" on any row in the table below.
            </div>
          )}

          {/* Comparison table — standard layout (stats as columns, players as rows) */}
          {comparisonPlayers.length > 0 && (
            <div className="comparison-table-wrapper">
              <table className="comparison-table">
                <thead>
                  <tr>
                    <th className="comparison-name-col">Name</th>
                    {stats.map((statDef) => (
                      <th key={statDef.key}>{statDef.label}</th>
                    ))}
                    <th className="comparison-actions-col"></th>
                  </tr>
                </thead>
                <tbody>
                  {displayPlayers.map((player) => (
                    <tr key={player.id ?? player.player_id}>
                      <td className="comparison-name-cell">{player.name}</td>
                      {stats.map((statDef) => {
                        const raw = player[statDef.key]
                        // Use formatRow (receives full player) for composite display (H/AB),
                        // otherwise use format function or raw value
                        const display = statDef.formatRow
                          ? statDef.formatRow(player)
                          : statDef.format
                            ? statDef.format(raw)
                            : (raw ?? '—')
                        const bestVal = getBestValue(statDef)
                        const isBest = bestVal != null && raw === bestVal
                        return (
                          <td
                            key={statDef.key}
                            className={isBest ? 'comparison-best' : undefined}
                          >
                            {display}
                          </td>
                        )
                      })}
                      <td className="comparison-actions-cell">
                        <button
                          className="btn-remove-compare"
                          onClick={() => onRemovePlayer(player.id ?? player.player_id)}
                          title="Remove from comparison"
                        >
                          ✕
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export default PlayerComparison
