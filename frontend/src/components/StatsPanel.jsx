/**
 * StatsPanel.jsx - Aggregated Statistics Display Component
 * =========================================================
 *
 * Displays two sections of statistics, both computed by Polars on the backend:
 *
 * 1. League Averages — overall averages across all players (from GET /players/stats)
 * 2. Team Breakdown — per-team aggregated stats with sortable columns
 *    (from GET /players/team-stats)
 *
 * Key React and data concepts demonstrated:
 * - Handling Polars' to_dict(as_series=False) response format
 * - Conditional rendering with && (short-circuit evaluation)
 * - Array.map() for rendering lists of data
 * - Defensive programming with ?. (optional chaining)
 * - useState for sort state management
 * - Column definitions array for DRY table rendering
 * - Comparator functions for multi-type sorting
 *
 * Polars response format note:
 *   The /players/stats endpoint uses Polars' .to_dict(as_series=False) which
 *   produces: { "avg_batting_average": [0.297], "avg_home_runs": [38.9] }
 *   Each value is a SINGLE-ELEMENT ARRAY (not a plain number). This is because
 *   Polars treats every column as a series (list), even if it has one value.
 *   The getValue() helper extracts element [0] from each array.
 *
 * Props:
 * @param {Object} stats - League average stats from GET /players/stats
 *   Format: { avg_batting_average: [0.297], avg_home_runs: [38.9], ... }
 * @param {Array} teamStats - Team aggregation data from GET /players/team-stats
 *   Format: [{ team: "Dodgers", player_count: 3, avg_ops: 0.965, ... }, ...]
 */

import { useState } from 'react'

/**
 * Column definitions for the team stats table.
 *
 * Each object describes one column:
 * - key: The property name on the team stats object
 * - label: The header text shown in the <th>
 * - format: Optional function to format the cell value
 * - bold: If true, renders the cell text in bold (used for team name)
 *
 * This is the same data-driven pattern used in PlayerTable.jsx.
 * Having column definitions in one place makes it easy to add new
 * team stats columns without touching the render logic.
 */
const TEAM_COLUMNS = [
  { key: 'team', label: 'Team', bold: true },
  { key: 'player_count', label: 'Players' },
  { key: 'avg_batting_average', label: 'AVG' },
  { key: 'avg_home_runs', label: 'HR' },
  { key: 'avg_rbi', label: 'RBI' },
  { key: 'avg_stolen_bases', label: 'SB' },
  { key: 'avg_ops', label: 'OPS' },
]

function StatsPanel({ stats, teamStats }) {
  // -------------------------------------------------------------------------
  // SORT STATE for team stats table
  // -------------------------------------------------------------------------
  // Same pattern as PlayerTable: track which column and direction.
  // Default sort is null (uses the backend's default order: by OPS descending).
  const [sortColumn, setSortColumn] = useState(null)
  const [sortDirection, setSortDirection] = useState('asc')
  const [teamBreakdownOpen, setTeamBreakdownOpen] = useState(false)

  // Guard clause: show a message if stats haven't loaded yet or are empty.
  // The stats?.detail check handles the backend's "No player data available" response.
  if (!stats || stats.detail) {
    return (
      <div className="stats-panel">
        <p>No aggregated stats available.</p>
      </div>
    )
  }

  /**
   * Extract a single value from the Polars response format.
   *
   * Polars' .to_dict(as_series=False) wraps every value in an array:
   *   { "avg_home_runs": [38.9] }  <-- note the array brackets
   *
   * This helper checks if the value is an array and extracts element [0].
   * If it's already a plain value (unlikely but safe), it returns it as-is.
   *
   * @param {string} key - The stat key (e.g., "avg_batting_average")
   * @returns {number|undefined} The numeric value, or undefined if not found
   */
  const getValue = (key) => {
    const val = stats[key]
    return Array.isArray(val) ? val[0] : val
  }

  /**
   * Handle a column header click to toggle sorting.
   * Same 3-click cycle as PlayerTable: asc -> desc -> none.
   *
   * @param {string} columnKey - The column key to sort by
   */
  const handleSort = (columnKey) => {
    if (sortColumn === columnKey) {
      if (sortDirection === 'asc') {
        setSortDirection('desc')
      } else {
        setSortColumn(null)
        setSortDirection('asc')
      }
    } else {
      setSortColumn(columnKey)
      setSortDirection('asc')
    }
  }

  /**
   * Get the sort indicator symbol for a column header.
   *
   * @param {string} columnKey - The column key to check
   * @returns {string} The indicator character (▲, ▼, or ↕)
   */
  const getSortIndicator = (columnKey) => {
    if (sortColumn !== columnKey) return ' ↕'
    return sortDirection === 'asc' ? ' ▲' : ' ▼'
  }

  /**
   * Sort the team stats array based on the current sort state.
   * Same sorting logic as PlayerTable — strings use localeCompare(),
   * numbers use subtraction, nulls go to the end.
   */
  const sortedTeamStats = (teamStats && sortColumn)
    ? teamStats.slice().sort((a, b) => {
        const aVal = a[sortColumn]
        const bVal = b[sortColumn]
        if (aVal == null && bVal == null) return 0
        if (aVal == null) return 1
        if (bVal == null) return -1
        const direction = sortDirection === 'asc' ? 1 : -1
        if (typeof aVal === 'string') {
          return direction * aVal.localeCompare(bVal)
        }
        return direction * (aVal - bVal)
      })
    : teamStats  // No sort active — use backend default order (by OPS desc)

  return (
    <div className="stats-panel">
      {/* ================================================================
          SECTION 1: League-Wide Averages
          These are computed by Polars using pl.col().mean() on all players.
          See backend main.py: get_aggregated_stats() for the Polars code.
          ================================================================ */}
      <h2>League Averages <span style={{ fontSize: '0.7rem', fontWeight: 400, color: '#8899aa' }}>(min 200 AB)</span></h2>
      <div className="stats-grid">
        {Object.keys(stats)
          .filter((key) => key.startsWith('avg_'))
          .map((key) => {
            const val = getValue(key)
            if (val == null) return null
            const statName = key.replace('avg_', '')
            const label = statName.split('_').map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(' ')
            const isFloat = statName === 'batting_average' || statName === 'ops'
            const decimals = isFloat ? 3 : 1
            return (
              <div className="stat-card" key={key}>
                <span className="stat-label">{label}</span>
                <span className="stat-value">{val.toFixed(decimals)}</span>
              </div>
            )
          })}
      </div>

      {/* ================================================================
          SECTION 2: Team Breakdown (Sortable)
          These are computed by Polars using .group_by("team").agg() which
          groups all players by their team and computes averages per group.
          See backend main.py: get_team_stats() for the Polars code.
          ================================================================ */}
      {sortedTeamStats && sortedTeamStats.length > 0 && (
        <>
          <h2
            className="collapsible-heading"
            onClick={() => setTeamBreakdownOpen(!teamBreakdownOpen)}
          >
            <span className="collapse-indicator">{teamBreakdownOpen ? '▾' : '▸'}</span>
            Team Breakdown
          </h2>
          {teamBreakdownOpen && (
            <table className="team-stats-table">
              <thead>
                <tr>
                  {TEAM_COLUMNS.map((col) => (
                    <th
                      key={col.key}
                      className="sortable-th"
                      onClick={() => handleSort(col.key)}
                    >
                      {col.label}
                      <span className="sort-indicator">{getSortIndicator(col.key)}</span>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {sortedTeamStats.map((team) => (
                  <tr key={team.team}>
                    {TEAM_COLUMNS.map((col) => (
                      <td key={col.key}>
                        {col.bold ? <strong>{team[col.key]}</strong> : team[col.key]}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}
    </div>
  )
}

export default StatsPanel
