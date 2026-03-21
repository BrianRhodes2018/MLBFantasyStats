/**
 * PitcherTable.jsx - Sortable Pitcher Data Table with Pagination
 * ===============================================================
 *
 * Displays MLB pitchers in a styled HTML table with their pitching stats.
 * All columns are sortable by clicking the header. Pagination shows 50
 * pitchers per page, with sorting applied to ALL data before pagination.
 *
 * This component mirrors the structure of PlayerTable.jsx but is tailored
 * for pitching statistics.
 */

import { useState } from 'react'
import { API_BASE } from '../config'

/**
 * Column definitions for the pitcher table.
 *
 * Each object describes one column with:
 * - key: Property name from the API response
 * - label: Header text displayed in the table
 * - tooltip: Hover explanation of the stat with benchmarks
 * - format: Optional function to format display value
 * - editable: Whether the field can be inline edited
 * - inputType/inputStep: HTML input attributes for editing
 * - isComputed: If true, value comes from computed stats array
 */
const COLUMNS = [
  {
    key: 'name',
    label: 'Name',
    tooltip: 'Pitcher\'s full name as registered with MLB.',
    editable: true
  },
  {
    key: 'team',
    label: 'Team',
    tooltip: 'The MLB franchise the pitcher is currently rostered with.',
    editable: true
  },
  {
    key: 'position',
    label: 'Pos',
    tooltip: 'Pitcher role.\nSP = Starting Pitcher (typically pitches 5-7 innings)\nRP = Relief Pitcher (typically pitches 1-2 innings)',
    format: (v) => v ?? '—',
    editable: true
  },
  {
    key: 'games',
    label: 'G',
    tooltip: 'Games (G)\nNumber of game appearances during the selected time period.\nOnly shown in rolling stats mode (Last 5/10/15/30 days).',
    rollingOnly: true,   // Only displayed when viewing rolling time-period stats
  },
  {
    key: 'wins',
    label: 'W',
    tooltip: 'Wins (W)\nAwarded to the pitcher of record when their team takes a lead that it never relinquishes.\nAverage: 8 | Good: 12 | Elite: 18+',
    editable: true,
    inputType: 'number'
  },
  {
    key: 'losses',
    label: 'L',
    tooltip: 'Losses (L)\nCharged to the pitcher of record when their team falls behind and never regains the lead.\nAverage: 8 | Good: <10 | Elite: <6',
    editable: true,
    inputType: 'number'
  },
  {
    key: 'era',
    label: 'ERA',
    tooltip: 'Earned Run Average (ERA)\n(Earned Runs ÷ Innings Pitched) × 9\nThe average number of earned runs allowed per nine innings. Lower is better.\nAverage: 4.00 | Good: 3.50 | Elite: <3.00',
    format: (v) => v?.toFixed(2),
    editable: true,
    inputType: 'number',
    inputStep: '0.01'
  },
  {
    key: 'whip',
    label: 'WHIP',
    tooltip: 'Walks + Hits per Inning Pitched (WHIP)\n(Walks + Hits) ÷ Innings Pitched\nMeasures how many baserunners a pitcher allows per inning. Lower is better.\nAverage: 1.30 | Good: 1.15 | Elite: <1.00',
    format: (v) => v?.toFixed(2),
    editable: true,
    inputType: 'number',
    inputStep: '0.01'
  },
  {
    key: 'innings_pitched',
    label: 'IP',
    tooltip: 'Innings Pitched (IP)\nThe number of innings a pitcher has recorded outs. Displayed as X.Y where Y represents partial innings (1 = 1/3, 2 = 2/3).\nAverage: 100 | Good: 180 | Elite: 200+',
    format: (v) => v?.toFixed(1),
    editable: true,
    inputType: 'number',
    inputStep: '0.1'
  },
  {
    key: 'hits_allowed',
    label: 'H',
    tooltip: 'Hits Allowed (H)\nThe number of hits given up by the pitcher.\nAverage: 150 | Good: <130 | Elite: <100',
    editable: true,
    inputType: 'number',
    mobileHide: true,
  },
  {
    key: 'earned_runs',
    label: 'ER',
    tooltip: 'Earned Runs (ER)\nRuns that score without the benefit of an error or passed ball. Used to calculate ERA.\nAverage: 70 | Good: <55 | Elite: <40',
    editable: true,
    inputType: 'number',
    mobileHide: true,
  },
  {
    key: 'walks',
    label: 'BB',
    tooltip: 'Walks / Bases on Balls (BB)\nWhen a pitcher throws four balls outside the strike zone, the batter advances to first base.\nAverage: 50 | Good: <40 | Elite: <30',
    editable: true,
    inputType: 'number',
    mobileHide: true,
  },
  {
    key: 'strikeouts',
    label: 'K',
    tooltip: 'Strikeouts (K)\nWhen a pitcher records three strikes against a batter, resulting in an out. Higher is better.\nAverage: 150 | Good: 200 | Elite: 250+',
    editable: true,
    inputType: 'number'
  },
  {
    key: 'home_runs_allowed',
    label: 'HR',
    tooltip: 'Home Runs Allowed (HR)\nThe number of home runs given up by the pitcher. Lower is better.\nAverage: 25 | Good: <18 | Elite: <12',
    editable: true,
    inputType: 'number',
    mobileHide: true,
  },
  {
    key: 'saves',
    label: 'SV',
    tooltip: 'Saves (SV)\nAwarded to a relief pitcher who finishes a game won by their team under specific circumstances (usually protecting a lead of 3 runs or less).\nAverage: 0 (starters) | Good: 25 | Elite: 40+',
    editable: true,
    inputType: 'number',
    mobileHide: true,
  },
  {
    key: 'quality_starts',
    label: 'QS',
    tooltip: 'Quality Starts (QS)\nA start where the pitcher goes 6+ innings and allows 3 or fewer earned runs.\nKey fantasy stat measuring pitcher reliability.\nAverage: 10 | Good: 18 | Elite: 24+',
    format: (v) => v ?? '—',
    editable: false,
    mobileHide: true,
  },
  {
    key: 'k_per_9',
    label: 'K/9',
    tooltip: 'Strikeouts per 9 Innings (K/9)\n(Strikeouts ÷ Innings Pitched) × 9\nMeasures a pitcher\'s ability to record strikeouts. Higher is better.\nAverage: 8.0 | Good: 9.5 | Elite: 11.0+',
    isComputed: true,
    mobileHide: true,
  },
  {
    key: 'bb_per_9',
    label: 'BB/9',
    tooltip: 'Walks per 9 Innings (BB/9)\n(Walks ÷ Innings Pitched) × 9\nMeasures a pitcher\'s control. Lower is better.\nAverage: 3.0 | Good: 2.5 | Elite: <2.0',
    isComputed: true,
    mobileHide: true,
  },
  {
    key: 'k_bb_ratio',
    label: 'K/BB',
    tooltip: 'Strikeout-to-Walk Ratio (K/BB)\nStrikeouts ÷ Walks\nMeasures a pitcher\'s command — strikeout ability relative to walks allowed. Higher is better.\nAverage: 2.5 | Good: 3.5 | Elite: 5.0+',
    isComputed: true,
    mobileHide: true,
  },
  {
    key: 'hr_per_9',
    label: 'HR/9',
    tooltip: 'Home Runs per 9 Innings (HR/9)\n(Home Runs Allowed ÷ Innings Pitched) × 9\nMeasures how frequently a pitcher gives up home runs. Lower is better.\nAverage: 1.2 | Good: 0.9 | Elite: <0.7',
    isComputed: true,
    mobileHide: true,
  },
  {
    key: 'fantasy_pts',
    label: 'Fantasy Pts',
    tooltip: 'Fantasy Points\nComputed based on the selected ESPN fantasy league\'s scoring settings.\nSelect a league in the header bar to see fantasy points.\nPoints = SUM(stat × league_point_value) for each scored category.',
    format: (v) => v != null ? v.toFixed(1) : '—',
    isComputed: true,   // Value comes from the fantasyPoints array, not the pitcher object
    isFantasy: true,    // Special flag: column only shows when a league is selected
  },
  {
    key: 'fantasy_pts_per_game',
    label: 'Pts/G',
    tooltip: 'Fantasy Points Per Game (Pts/G)\nTotal fantasy points ÷ games appeared.\nCompares per-game fantasy value across pitchers with different workloads.\nHigher = more valuable on a per-game basis.',
    format: (v) => v != null ? v.toFixed(1) : '—',
    isComputed: true,
    isFantasy: true,
  },
]

function PitcherTable({ pitchers, computed, fantasyPoints, onPitcherUpdated, isRolling, onPitcherClick, comparisonIds, onAddToComparison }) {
  // Filter columns based on season vs rolling mode.
  // - rollingOnly columns (like 'games') only show in rolling mode
  // - In rolling mode, columns marked isComputed that DON'T exist in rolling
  //   data are hidden (like k_bb_ratio and win_pct which aren't returned by
  //   the rolling endpoint). Columns like k_per_9 and hr_per_9 ARE in the
  //   rolling data, so they show in both modes.
  //
  // Rolling pitcher endpoint returns: name, team, games, innings_pitched,
  //   era, whip, k_per_9, hr_per_9, wins, losses, saves, quality_starts,
  //   strikeouts, walks, earned_runs
  //
  // Columns NOT in rolling data: games_started, hits_allowed, home_runs_allowed,
  //   bb_per_9, k_bb_ratio, win_pct
  const rollingPitcherKeys = [
    'name', 'team', 'games', 'innings_pitched', 'era', 'whip',
    'k_per_9', 'hr_per_9', 'wins', 'losses', 'saves', 'quality_starts',
    'strikeouts', 'walks', 'earned_runs',
  ]
  const activeColumns = COLUMNS.filter((col) => {
    if (col.rollingOnly && !isRolling) return false
    // Hide the Fantasy Pts column when no league is selected
    if (col.isFantasy && (!fantasyPoints || fantasyPoints.length === 0)) return false
    if (isRolling) {
      // In rolling mode, only show columns that exist in rolling data
      return rollingPitcherKeys.includes(col.key)
    }
    return true
  })

  // Sort state
  const [sortColumn, setSortColumn] = useState(null)
  const [sortDirection, setSortDirection] = useState('asc')

  // Pagination state
  const [currentPage, setCurrentPage] = useState(0)
  const PITCHERS_PER_PAGE = 50

  // Edit state
  const [editingId, setEditingId] = useState(null)
  const [editForm, setEditForm] = useState({})
  const [editMessage, setEditMessage] = useState(null)

  if (!pitchers || !pitchers.length) {
    return <p>No pitchers found. Run: python mlb_data_fetcher.py --pitchers --save</p>
  }

  const getComputedStats = (pitcherId) => {
    return computed?.find((c) => c.id === pitcherId)
  }

  const handleSort = (columnKey) => {
    setCurrentPage(0)
    if (sortColumn === columnKey) {
      setSortDirection(sortDirection === 'asc' ? 'desc' : 'asc')
    } else {
      setSortColumn(columnKey)
      setSortDirection('asc')
    }
  }

  const getSortIndicator = (columnKey) => {
    if (sortColumn !== columnKey) return ' ↕'
    return sortDirection === 'asc' ? ' ▲' : ' ▼'
  }

  // Edit handlers
  const handleEditClick = (pitcher) => {
    setEditingId(pitcher.id)
    setEditForm({
      name: pitcher.name,
      team: pitcher.team,
      position: pitcher.position ?? '',
      wins: pitcher.wins,
      losses: pitcher.losses,
      era: pitcher.era,
      whip: pitcher.whip,
      innings_pitched: pitcher.innings_pitched,
      hits_allowed: pitcher.hits_allowed,
      earned_runs: pitcher.earned_runs,
      walks: pitcher.walks,
      strikeouts: pitcher.strikeouts,
      home_runs_allowed: pitcher.home_runs_allowed ?? '',
      saves: pitcher.saves ?? '',
    })
    setEditMessage(null)
  }

  const handleCancelEdit = () => {
    setEditingId(null)
    setEditForm({})
    setEditMessage(null)
  }

  const handleEditChange = (e) => {
    setEditForm({ ...editForm, [e.target.name]: e.target.value })
  }

  const handleSaveEdit = async (pitcher) => {
    const payload = {
      name: editForm.name,
      team: editForm.team,
      position: editForm.position || null,
      wins: parseInt(editForm.wins, 10),
      losses: parseInt(editForm.losses, 10),
      era: parseFloat(editForm.era),
      whip: parseFloat(editForm.whip),
      innings_pitched: parseFloat(editForm.innings_pitched),
      hits_allowed: parseInt(editForm.hits_allowed, 10),
      earned_runs: parseInt(editForm.earned_runs, 10),
      walks: parseInt(editForm.walks, 10),
      strikeouts: parseInt(editForm.strikeouts, 10),
      home_runs_allowed: editForm.home_runs_allowed ? parseInt(editForm.home_runs_allowed, 10) : null,
      saves: editForm.saves ? parseInt(editForm.saves, 10) : null,
    }

    try {
      const res = await fetch(`${API_BASE}/pitchers/${pitcher.id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })

      const responseData = await res.json()

      if (res.ok) {
        setEditMessage({ text: `[${responseData.code}] ${responseData.message}`, type: 'success' })
        setEditingId(null)
        setEditForm({})
        if (onPitcherUpdated) onPitcherUpdated()
        setTimeout(() => setEditMessage(null), 4000)
      } else {
        const errorMsg = responseData.message || responseData.detail || 'Failed to update pitcher'
        setEditMessage({ text: `[${res.status}] ${errorMsg}`, type: 'error' })
        setTimeout(() => setEditMessage(null), 4000)
      }
    } catch (error) {
      setEditMessage({ text: `[Error] Network error: ${error.message}`, type: 'error' })
      setTimeout(() => setEditMessage(null), 4000)
    }
  }

  // Merge pitchers with computed stats.
  // In rolling mode, skip the merge since rolling data already contains
  // all the stats we need (ERA, WHIP, K/9, HR/9 computed from game logs).
  const mergedPitchers = pitchers.map((pitcher) => {
    if (isRolling) return pitcher  // Rolling data is already complete
    const comp = getComputedStats(pitcher.id)
    // Merge fantasy points — same pattern as computed stats
    const fantasyPt = fantasyPoints?.find((f) => f.id === pitcher.id)
    return { ...pitcher, ...(comp || {}), ...(fantasyPt || {}) }
  })

  // Sort the merged data
  const sortedPitchers = sortColumn
    ? mergedPitchers.slice().sort((a, b) => {
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
    : mergedPitchers

  // Pagination
  const totalPages = Math.ceil(sortedPitchers.length / PITCHERS_PER_PAGE)
  const startIndex = currentPage * PITCHERS_PER_PAGE
  const endIndex = startIndex + PITCHERS_PER_PAGE
  const paginatedPitchers = sortedPitchers.slice(startIndex, endIndex)

  const goToFirstPage = () => setCurrentPage(0)
  const goToPrevPage = () => setCurrentPage((prev) => Math.max(0, prev - 1))
  const goToNextPage = () => setCurrentPage((prev) => Math.min(totalPages - 1, prev + 1))
  const goToLastPage = () => setCurrentPage(totalPages - 1)

  return (
    <div className="player-table pitcher-table">
      {/* Table title and position filter are now in the unified header bar
          rendered by App.jsx directly above this component. */}

      {editMessage && (
        <div className={`form-message form-message-${editMessage.type}`}>
          {editMessage.text}
        </div>
      )}

      <table>
        <thead>
          <tr>
            {activeColumns.map((col) => (
              <th
                key={col.key}
                className={`sortable-th has-tooltip${col.key === 'name' ? ' sticky-name' : ''}${col.mobileHide ? ' mobile-hide' : ''}`}
                onClick={() => handleSort(col.key)}
              >
                <span className="column-header-content">
                  {col.label}
                  <span className="sort-indicator">{getSortIndicator(col.key)}</span>
                  {col.tooltip && (
                    <span className="column-tooltip">{col.tooltip}</span>
                  )}
                </span>
              </th>
            ))}
            {/* Actions column hidden in rolling mode (aggregated data is read-only) */}
            {!isRolling && <th>Actions</th>}
          </tr>
        </thead>
        <tbody>
          {paginatedPitchers.map((pitcher, index) => {
            // In rolling mode, editing is disabled
            const isEditing = !isRolling && editingId === pitcher.id
            // Use pitcher.id for season data, pitcher.player_id for rolling data
            const rowKey = pitcher.id ?? pitcher.player_id ?? index

            return (
              <tr key={rowKey}>
                {activeColumns.map((col) => {
                  if (isEditing && col.editable) {
                    return (
                      <td key={col.key} className={col.mobileHide ? 'mobile-hide' : undefined}>
                        <input
                          className="edit-input"
                          name={col.key}
                          type={col.inputType || 'text'}
                          step={col.inputStep}
                          value={editForm[col.key] ?? ''}
                          onChange={handleEditChange}
                        />
                      </td>
                    )
                  }

                  const rawValue = pitcher[col.key]
                  const displayValue = col.format
                    ? col.format(rawValue)
                    : col.isComputed
                      ? (rawValue ?? '—')
                      : rawValue

                  return (
                    <td
                      key={col.key}
                      className={[
                        col.isComputed ? 'computed-stat' : '',
                        col.mobileHide ? 'mobile-hide' : '',
                        col.key === 'name' ? 'sticky-name' : '',
                      ].filter(Boolean).join(' ') || undefined}
                    >
                      {/* Name column: render as a clickable link that opens the player detail modal.
                          Other columns render as plain text. */}
                      {col.key === 'name' && onPitcherClick ? (
                        <span
                          className="player-name-link"
                          onClick={() => onPitcherClick(pitcher)}
                          role="button"
                          tabIndex={0}
                          onKeyDown={(e) => e.key === 'Enter' && onPitcherClick(pitcher)}
                        >
                          {displayValue}
                        </span>
                      ) : (
                        displayValue
                      )}
                    </td>
                  )
                })}

                {/* Actions cell: Hidden in rolling mode (aggregated data is read-only) */}
                {!isRolling && (
                  <td className="actions-cell">
                    {isEditing ? (
                      <>
                        <button className="btn-save" onClick={() => handleSaveEdit(pitcher)}>Save</button>
                        <button className="btn-cancel" onClick={handleCancelEdit}>Cancel</button>
                      </>
                    ) : (
                      <>
                        <button className="btn-edit" onClick={() => handleEditClick(pitcher)}>Edit</button>
                        {onAddToComparison && (
                          <button
                            className={`btn-compare ${comparisonIds?.has(pitcher.id) ? 'active' : ''}`}
                            onClick={() => onAddToComparison(pitcher, 'pitcher')}
                            disabled={comparisonIds?.has(pitcher.id)}
                            title={comparisonIds?.has(pitcher.id) ? 'Already in comparison' : 'Add to comparison'}
                          >
                            {comparisonIds?.has(pitcher.id) ? '✓' : 'Compare'}
                          </button>
                        )}
                      </>
                    )}
                  </td>
                )}
              </tr>
            )
          })}
        </tbody>
      </table>

      {totalPages > 1 && (
        <div className="pagination-controls">
          <button
            className="pagination-btn"
            onClick={goToFirstPage}
            disabled={currentPage === 0}
            title="First page"
          >
            « First
          </button>
          <button
            className="pagination-btn"
            onClick={goToPrevPage}
            disabled={currentPage === 0}
            title="Previous page"
          >
            ‹ Prev
          </button>

          <span className="pagination-info">
            Page {currentPage + 1} of {totalPages}
            <span className="pagination-range">
              (showing {startIndex + 1}-{Math.min(endIndex, sortedPitchers.length)} of {sortedPitchers.length} pitchers)
            </span>
          </span>

          <button
            className="pagination-btn"
            onClick={goToNextPage}
            disabled={currentPage === totalPages - 1}
            title="Next page"
          >
            Next ›
          </button>
          <button
            className="pagination-btn"
            onClick={goToLastPage}
            disabled={currentPage === totalPages - 1}
            title="Last page"
          >
            Last »
          </button>
        </div>
      )}
    </div>
  )
}

export default PitcherTable
