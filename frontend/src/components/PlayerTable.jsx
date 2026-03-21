/**
 * PlayerTable.jsx - Sortable Player Data Table with Inline Editing
 * =================================================================
 *
 * Displays all MLB players in a styled HTML table with raw stats
 * (from the database), position, and computed stats (calculated by
 * Polars on the backend). All columns are sortable by clicking the header.
 * Players can be edited inline by clicking the "Edit" button.
 *
 * Key React concepts demonstrated:
 * - useState for sort state and edit state: Multiple independent state
 *   variables can coexist in the same component. React tracks each one
 *   separately and only re-renders when one changes.
 * - Computed values: sortedPlayers is derived from props + state on every render.
 *   This is a common React pattern — instead of storing sorted data in state,
 *   we compute it on each render from the source data + sort settings.
 * - Array.slice().sort(): Creates a sorted COPY without mutating the original array.
 *   React props should NEVER be mutated directly — they belong to the parent.
 * - Comparator functions: Custom logic for sorting strings vs numbers vs nulls.
 * - Column definitions array: A data-driven approach to rendering table headers
 *   and cells, reducing repetitive JSX. Adding a new column only requires one
 *   new object in the COLUMNS array — no changes to the render logic.
 * - Inline editing: Clicking "Edit" replaces cell text with input fields.
 *   This is controlled by comparing editingId to each player's ID.
 * - Conditional rendering: Using ternary operators (condition ? A : B) and
 *   short-circuit evaluation (condition && element) to show/hide UI.
 *
 * How inline editing works (data flow):
 *   1. User clicks "Edit" → handleEditClick copies player data into editForm state
 *   2. editingId is set to this player's ID → triggers re-render
 *   3. During render, each row checks: is my ID === editingId?
 *      - If yes: render <input> fields (edit mode) with values from editForm
 *      - If no: render normal <td> text (display mode)
 *   4. User modifies inputs → handleEditChange updates editForm state
 *   5. User clicks "Save" → handleSaveEdit sends PUT /players/{id} to backend
 *   6. On success: clear editingId, call onPlayerUpdated to refresh all data
 *
 * API endpoint used for updates:
 *   PUT /players/{player_id} — sends JSON body with updated fields
 *   Returns: { code: 200, message: "Player 'Name' updated successfully", data: {...} }
 *
 * Props:
 * @param {Array} players - Array of player objects from GET /players/ or search results.
 *   Each object has: { id, name, team, position, batting_average, home_runs, rbi, stolen_bases, ops }
 * @param {Array} computed - Array of computed stat objects from GET /players/computed.
 *   Each object has: { id, name, team, power_index, contact_rating, speed_score }
 * @param {string} tableTitle - Optional custom title for the table (e.g., "Search Results").
 *   Defaults to "All Players" when not provided.
 * @param {Function} onPlayerUpdated - Callback function to call after a player is
 *   successfully updated. The parent (App.jsx) uses this to re-fetch all data
 *   so the table, stats, and computed values reflect the changes.
 */

import { useState } from 'react'
import { API_BASE } from '../config'

/**
 * Column definitions for the player table.
 *
 * Each object describes one column:
 * - key: The property name on the player/computed object to display and sort by.
 *         This MUST match the exact key from the API response (e.g., "batting_average").
 * - label: The header text shown in the <th> element.
 * - tooltip: Description of the stat shown on hover. Explains what the stat means
 *            and how it's calculated. This helps users understand the data.
 * - format: Optional function to format the cell value for display.
 *           e.g., (v) => v?.toFixed(3) converts 0.28700000001 to "0.287".
 *           The ?. (optional chaining) prevents errors if v is null/undefined.
 * - isComputed: If true, the value comes from the computed stats array (not the
 *               player object). Computed stats are calculated by Polars on the backend
 *               and merged into each player row in the mergedPlayers step below.
 * - editable: If true, this field can be edited inline. Computed stats are NOT editable
 *             because they're derived from other fields — editing them directly wouldn't
 *             make sense (they'd just get recalculated on the next data fetch).
 * - inputType: The HTML input type for editing. "number" restricts input to numeric
 *              values and shows increment/decrement buttons. Default is "text".
 * - inputStep: The step attribute for number inputs. Controls the increment size
 *              when using the up/down buttons. "0.001" allows 3 decimal places
 *              (for batting_average and ops), while no step defaults to 1 (integers).
 *
 * Using a definitions array (instead of hardcoding each <th> and <td>) makes
 * the code DRY and makes it easy to add/remove columns in one place.
 */
const COLUMNS = [
  {
    key: 'name',
    label: 'Name',
    tooltip: 'Player\'s full name as registered with MLB.',
    editable: true
  },
  {
    key: 'team',
    label: 'Team',
    tooltip: 'The MLB franchise the player is currently rostered with.',
    editable: true
  },
  {
    key: 'position',
    label: 'Pos',
    tooltip: 'Primary defensive position.\nC=Catcher, 1B=First Base, 2B=Second Base, 3B=Third Base, SS=Shortstop, LF=Left Field, CF=Center Field, RF=Right Field, DH=Designated Hitter',
    format: (v) => v ?? '—',
    editable: true,
    mobileHide: true,    // Hidden on phones — saves horizontal space
  },
  {
    key: 'games',
    label: 'G',
    tooltip: 'Games Played (G)\nNumber of games the player appeared in during the selected time period.\nOnly shown in rolling stats mode (Last 5/10/15/30 days).',
    rollingOnly: true,   // Only displayed when viewing rolling time-period stats
  },
  {
    key: 'at_bats',
    label: 'H/AB',
    tooltip: 'Hits / At Bats (H/AB)\nHits: total base hits. At Bats: official plate appearances excluding walks, HBP, sacrifices.\nDisplayed as H/AB (e.g., 150/525). Sorts and edits by AB.\nAverage AB: 450 | Good: 550 | Elite: 600+',
    editable: true,
    inputType: 'number',
    // formatRow receives the full player object so we can display "hits/at_bats"
    formatRow: (player) => {
      const h = player.hits ?? '—'
      const ab = player.at_bats ?? '—'
      return `${h}/${ab}`
    },
  },
  {
    key: 'batting_average',
    label: 'AVG',
    tooltip: 'Batting Average (AVG)\nHits ÷ At Bats\nMeasures the rate at which a batter gets a base hit.\nAverage: .250 | Good: .270 | Elite: .300+',
    format: (v) => v?.toFixed(3),
    editable: true,
    inputType: 'number',
    inputStep: '0.001'
  },
  {
    key: 'home_runs',
    label: 'HR',
    tooltip: 'Home Runs (HR)\nA hit where the batter rounds all bases and scores on the same play, typically by hitting the ball over the outfield fence.\nAverage: 15 | Good: 30 | Elite: 40+',
    editable: true,
    inputType: 'number'
  },
  {
    key: 'rbi',
    label: 'RBI',
    tooltip: 'Runs Batted In (RBI)\nThe number of runs that score as a direct result of a batter\'s plate appearance (excluding errors and double plays).\nAverage: 60 | Good: 90 | Elite: 110+',
    editable: true,
    inputType: 'number'
  },
  {
    key: 'stolen_bases',
    label: 'SB',
    tooltip: 'Stolen Bases (SB)\nWhen a baserunner advances to the next base during a pitch without the ball being put in play.\nAverage: 5 | Good: 20 | Elite: 40+',
    editable: true,
    inputType: 'number',
    mobileHide: true,    // Hidden on phones — less critical for quick view
  },
  {
    key: 'runs',
    label: 'R',
    tooltip: 'Runs (R)\nThe number of times a player crosses home plate to score.\nAverage: 60 | Good: 90 | Elite: 110+',
    editable: true,
    inputType: 'number',
    mobileHide: true,    // Hidden on phones — less critical for quick view
  },
  {
    key: 'strikeouts',
    label: 'K',
    tooltip: 'Strikeouts (K)\nWhen a batter accumulates three strikes during an at-bat, resulting in an out. Lower is better for batters.\nAverage: 120 | Good: 80 | Elite: <60',
    editable: true,
    inputType: 'number',
    mobileHide: true,    // Hidden on phones — less critical for quick view
  },
  {
    key: 'total_bases',
    label: 'TB',
    tooltip: 'Total Bases (TB)\nThe total number of bases gained on hits: 1 for single, 2 for double, 3 for triple, 4 for home run.\nAverage: 200 | Good: 280 | Elite: 340+',
    editable: true,
    inputType: 'number',
    mobileHide: true,    // Hidden on phones — less critical for quick view
  },
  {
    key: 'obp',
    label: 'OBP',
    tooltip: 'On-Base Percentage (OBP)\n(Hits + Walks + HBP) ÷ (AB + Walks + HBP + SF)\nMeasures how often a batter reaches base safely. Unlike batting average, OBP credits walks and hit-by-pitches — a patient hitter who draws walks is still getting on base.\nAverage: .320 | Good: .350 | Elite: .400+',
    format: (v) => v?.toFixed(3),
    // OBP is a computed stat — it's calculated from raw columns (AVG, AB, BB, HBP, SF)
    // on the backend, not stored directly in the database. It comes from either:
    //   1. The /players/computed endpoint (merged into player data by PlayerTable)
    //   2. The /players/search results (included directly in search response)
    //   3. The /players/rolling-stats endpoint (computed from game logs)
    isComputed: true,
    mobileHide: true,    // Hidden on phones — OPS covers this
  },
  {
    key: 'ops',
    label: 'OPS',
    tooltip: 'On-base Plus Slugging (OPS)\nOBP + SLG\nOBP = (H + BB + HBP) ÷ (AB + BB + HBP + SF)\nSLG = Total Bases ÷ At Bats\nCombines ability to get on base with power.\nAverage: .710 | Good: .800 | Elite: .900+',
    format: (v) => v?.toFixed(3),
    editable: true,
    inputType: 'number',
    inputStep: '0.001'
  },
  {
    key: 'power_index',
    label: 'Power Idx',
    tooltip: 'Power Index (Custom)\nHR × OPS\nCombines home run volume with overall hitting efficiency.\nAverage: 20 | Good: 35 | Elite: 50+',
    isComputed: true,
    mobileHide: true,    // Hidden on phones — advanced stat, tap player for detail
  },
  {
    key: 'speed_score',
    label: 'Speed',
    tooltip: 'Speed Score (Custom)\nSB / (SB + 10) × 100\nNormalized 0-100 scale with diminishing returns at high SB counts.\nAverage: 30 | Good: 60 | Elite: 80+',
    isComputed: true,
    mobileHide: true,    // Hidden on phones — advanced stat, tap player for detail
  },
  {
    key: 'fantasy_pts',
    label: 'Fantasy Pts',
    tooltip: 'Fantasy Points\nComputed based on the selected ESPN fantasy league\'s scoring settings.\nSelect a league in the header bar to see fantasy points.\nPoints = SUM(stat × league_point_value) for each scored category.',
    format: (v) => v != null ? v.toFixed(1) : '—',
    isComputed: true,   // Value comes from the fantasyPoints array, not the player object
    isFantasy: true,    // Special flag: column only shows when a league is selected
  },
  {
    key: 'fantasy_pts_per_game',
    label: 'Pts/G',
    tooltip: 'Fantasy Points Per Game (Pts/G)\nTotal fantasy points ÷ games played.\nCompares per-game fantasy value across players with different games played.\nHigher = more valuable on a per-game basis.',
    format: (v) => v != null ? v.toFixed(1) : '—',
    isComputed: true,
    isFantasy: true,
  },
]

function PlayerTable({ players, computed, fantasyPoints, onPlayerUpdated, isRolling, onPlayerClick, comparisonIds, onAddToComparison }) {
  // -------------------------------------------------------------------------
  // SORT STATE
  // -------------------------------------------------------------------------
  // sortColumn: Which column key is currently sorted (null = no sort active).
  //   When null, players appear in their original order from the API.
  // sortDirection: 'asc' for ascending (A-Z, 0-9) or 'desc' for descending.
  const [sortColumn, setSortColumn] = useState(null)
  const [sortDirection, setSortDirection] = useState('asc')

  // -------------------------------------------------------------------------
  // PAGINATION STATE
  // -------------------------------------------------------------------------
  // currentPage: The current page number (0-indexed internally for easier math).
  //   Page 0 = first 50 players, Page 1 = next 50, etc.
  // PLAYERS_PER_PAGE: How many players to show on each page (50).
  const [currentPage, setCurrentPage] = useState(0)
  const PLAYERS_PER_PAGE = 50

  // -------------------------------------------------------------------------
  // EDIT STATE
  // -------------------------------------------------------------------------
  // editingId: The database ID of the player currently being edited.
  //   null means no row is in edit mode. Only ONE row can be edited at a time.
  //   When this changes, React re-renders and the matching row switches to
  //   input fields instead of text.
  const [editingId, setEditingId] = useState(null)

  // editForm: An object holding the current values of all editable fields
  //   for the row being edited. This is a "controlled form" pattern — the
  //   input values are driven by this state, not by the DOM.
  //   Example: { name: "Aaron Judge", team: "Yankees", home_runs: 52, ... }
  const [editForm, setEditForm] = useState({})

  // editMessage: Feedback message shown above the table after a save attempt.
  //   null = no message. Object = { text: string, type: "success"|"error" }
  //   Same pattern used in PlayerForm for the "Player created successfully" message.
  const [editMessage, setEditMessage] = useState(null)

  // Filter columns based on the current mode (season vs rolling).
  // - rollingOnly columns (like 'games') only show in rolling mode
  // - In rolling mode, only show columns that exist in the rolling data response
  //
  // Rolling batter endpoint returns: player_id, name, team, games, at_bats,
  //   batting_average, home_runs, rbi, runs, stolen_bases, strikeouts, ops, total_bases
  //
  // Columns NOT in rolling data: position (not relevant for rolling aggregation),
  //   power_index, speed_score (computed stats not calculated for rolling)
  // OBP is included here because the rolling-stats endpoint now returns it.
  // It's computed from game log data: (H + BB + HBP) / (AB + BB + HBP + SF).
  const rollingBatterKeys = [
    'name', 'team', 'games', 'at_bats', 'batting_average', 'obp',
    'home_runs', 'rbi', 'runs', 'stolen_bases', 'strikeouts', 'ops', 'total_bases',
  ]
  const activeColumns = COLUMNS.filter((col) => {
    if (col.rollingOnly && !isRolling) return false
    // Hide the Fantasy Pts column when no league is selected or no data yet.
    // This prevents showing an empty "Fantasy Pts" column with all dashes.
    if (col.isFantasy && (!fantasyPoints || fantasyPoints.length === 0)) return false
    if (isRolling) {
      // In rolling mode, only show columns that exist in rolling data
      return rollingBatterKeys.includes(col.key)
    }
    return true
  })

  // Early return if there's no data to show.
  // This prevents rendering an empty table which would look broken.
  if (!players.length) {
    return <p>No players found.</p>
  }

  /**
   * Look up computed stats for a specific player by their ID.
   *
   * Array.find() searches through the `computed` array and returns the first
   * object where the condition is true (matching ID). Returns undefined if
   * no match is found (e.g., if computed stats haven't loaded yet).
   *
   * The computed stats come from GET /players/computed, which uses Polars
   * .with_columns() to calculate power_index, contact_rating, and speed_score.
   *
   * @param {number} playerId - The player's database ID
   * @returns {Object|undefined} The computed stats object, or undefined
   */
  const getComputedStats = (playerId) => {
    return computed.find((c) => c.id === playerId)
  }

  /**
   * Handle a column header click to toggle sorting.
   *
   * Sorting behavior (2-click cycle per your request):
   * - First click on a column: sort ascending (A-Z, 0-9, lowest first)
   * - Second click on the SAME column: sort descending (Z-A, 9-0, highest first)
   * - Click on a DIFFERENT column: sort that column ascending
   *
   * IMPORTANT: Sorting applies to ALL players FIRST, then pagination shows
   * the current page of that sorted list. This ensures you see the top/bottom
   * players across the entire dataset, not just the current page.
   *
   * Also resets to page 1 whenever sort changes, so you see the beginning
   * of the newly sorted list.
   *
   * @param {string} columnKey - The column key to sort by (e.g., "home_runs")
   */
  const handleSort = (columnKey) => {
    // Reset to first page whenever sort changes
    setCurrentPage(0)

    if (sortColumn === columnKey) {
      // Same column clicked — toggle between asc and desc
      setSortDirection(sortDirection === 'asc' ? 'desc' : 'asc')
    } else {
      // Different column — start with ascending
      setSortColumn(columnKey)
      setSortDirection('asc')
    }
  }

  /**
   * Get the sort indicator symbol for a column header.
   *
   * Shows ▲ for ascending, ▼ for descending on the active sort column.
   * Shows ↕ on all other columns to indicate they are sortable.
   * These symbols are rendered in a <span> next to the column label.
   *
   * @param {string} columnKey - The column key to check
   * @returns {string} The indicator character
   */
  const getSortIndicator = (columnKey) => {
    if (sortColumn !== columnKey) return ' ↕'
    return sortDirection === 'asc' ? ' ▲' : ' ▼'
  }

  // -------------------------------------------------------------------------
  // INLINE EDIT HANDLERS
  // -------------------------------------------------------------------------
  // These functions manage the edit lifecycle:
  //   Edit click → Form changes → Save or Cancel

  /**
   * Start editing a player row.
   *
   * Copies the player's current database values into the editForm state
   * so that the input fields are pre-filled with the existing data.
   * The user can then modify individual fields and click Save.
   *
   * Why copy to state instead of editing props directly?
   * React props are READ-ONLY. The player object belongs to the parent
   * component (App.jsx). To modify values, we need our own local copy
   * in state. This is a fundamental React pattern.
   *
   * The ?? '' for position handles null values — HTML inputs don't accept
   * null as a value (they'd show "null" as text), so we convert to empty string.
   *
   * @param {Object} player - The player object for the row being edited
   */
  const handleEditClick = (player) => {
    setEditingId(player.id)
    setEditForm({
      name: player.name,
      team: player.team,
      position: player.position ?? '',   // Convert null -> '' for input compatibility
      at_bats: player.at_bats ?? '',
      batting_average: player.batting_average,
      home_runs: player.home_runs,
      rbi: player.rbi,
      stolen_bases: player.stolen_bases,
      runs: player.runs ?? '',
      strikeouts: player.strikeouts ?? '',
      total_bases: player.total_bases ?? '',
      ops: player.ops,
    })
    setEditMessage(null)  // Clear any previous save message
  }

  /**
   * Cancel editing — discard all changes and exit edit mode.
   *
   * Resets all edit-related state back to defaults:
   * - editingId → null (no row in edit mode)
   * - editForm → {} (clear the form data)
   * - editMessage → null (hide any messages)
   *
   * The original player data is untouched because we were editing
   * a copy in editForm, not the actual props.
   */
  const handleCancelEdit = () => {
    setEditingId(null)
    setEditForm({})
    setEditMessage(null)
  }

  /**
   * Handle changes to edit form inputs.
   *
   * This is a single handler for ALL edit inputs, using the input's `name`
   * attribute to determine which field to update. Same pattern as PlayerForm.
   *
   * How it works:
   * 1. e.target.name gives us the field name (e.g., "home_runs")
   * 2. e.target.value gives us the new value the user typed
   * 3. ...editForm spreads the existing values (keeps other fields unchanged)
   * 4. [e.target.name]: e.target.value updates just the one field that changed
   *
   * The [computed property] syntax uses the variable's VALUE as the object key.
   * So if e.target.name is "home_runs", it becomes { ...editForm, home_runs: "52" }.
   *
   * Note: All input values are STRINGS at this point — they get converted to
   * numbers in handleSaveEdit before being sent to the API.
   *
   * @param {Event} e - The input change event
   */
  const handleEditChange = (e) => {
    setEditForm({ ...editForm, [e.target.name]: e.target.value })
  }

  /**
   * Save the edited player — send a PUT request to the backend.
   *
   * This sends ALL editable fields to the backend (not just changed ones).
   * The backend's PlayerUpdate schema accepts all fields as optional, so
   * sending unchanged fields is harmless — they just get set to the same value.
   *
   * Steps:
   * 1. Convert string input values to proper types (parseFloat/parseInt)
   *    because HTML inputs always return strings, but the API expects numbers.
   * 2. Send PUT /players/{id} with the JSON payload.
   * 3. Parse the response — the backend returns ApiResponse format:
   *    { code: 200, message: "Player 'Name' updated", data: {...} }
   * 4. On success: show message, exit edit mode, trigger parent refresh.
   * 5. On error: show error message but KEEP edit mode open so user can fix.
   *
   * The PUT request goes through Vite's proxy (defined in vite.config.js):
   *   fetch('/players/3') → Vite forwards to → http://localhost:8000/players/3
   *
   * @param {Object} player - The original player object (used for player.id)
   */
  const handleSaveEdit = async (player) => {
    // Build the payload with properly typed values.
    // HTML inputs always return strings, but our FastAPI endpoint expects:
    // - name, team, position: str (already strings, no conversion needed)
    // - batting_average, ops: float (use parseFloat)
    // - home_runs, rbi, stolen_bases: int (use parseInt with base 10)
    const payload = {
      name: editForm.name,
      team: editForm.team,
      position: editForm.position || null,  // Send null if position is empty string
      at_bats: editForm.at_bats ? parseInt(editForm.at_bats, 10) : null,
      batting_average: parseFloat(editForm.batting_average),
      home_runs: parseInt(editForm.home_runs, 10),    // 10 = base-10 (decimal system)
      rbi: parseInt(editForm.rbi, 10),
      stolen_bases: parseInt(editForm.stolen_bases, 10),
      runs: editForm.runs ? parseInt(editForm.runs, 10) : null,
      strikeouts: editForm.strikeouts ? parseInt(editForm.strikeouts, 10) : null,
      total_bases: editForm.total_bases ? parseInt(editForm.total_bases, 10) : null,
      ops: parseFloat(editForm.ops),
    }

    try {
      // Template literal builds the URL with the player's ID:
      // `/players/${player.id}` becomes "/players/3" if player.id is 3.
      //
      // fetch() with method: 'PUT' tells the server we're updating an existing resource.
      // PUT semantics: "replace the resource at this URL with the provided data."
      // Headers tell the server we're sending JSON in the request body.
      const res = await fetch(`${API_BASE}/players/${player.id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })

      // Parse the JSON response. The backend returns ApiResponse:
      // { code: 200, message: "Player 'Aaron Judge' updated successfully", data: {...} }
      const responseData = await res.json()

      if (res.ok) {
        // res.ok is true for HTTP 200-299 status codes.
        // Show the API's message with its code (e.g., "[200] Player 'Aaron Judge' updated").
        setEditMessage({ text: `[${responseData.code}] ${responseData.message}`, type: 'success' })

        // Exit edit mode — clear the editing state.
        setEditingId(null)
        setEditForm({})

        // Notify the parent (App.jsx) that data changed.
        // The parent will re-fetch ALL data (players, stats, computed, team-stats)
        // so everything updates — not just the table, but also league averages,
        // team breakdowns, and computed stats like power_index.
        if (onPlayerUpdated) onPlayerUpdated()

        // Auto-clear the success message after 4 seconds (same UX as PlayerForm).
        setTimeout(() => setEditMessage(null), 4000)
      } else {
        // HTTP error (4xx or 5xx). Show the error but keep edit mode open
        // so the user can fix their input without re-entering everything.
        // FastAPI validation errors (422) include a "detail" field.
        const errorMsg = responseData.message || responseData.detail || 'Failed to update player'
        setEditMessage({ text: `[${res.status}] ${errorMsg}`, type: 'error' })
        setTimeout(() => setEditMessage(null), 4000)
      }
    } catch (error) {
      // Network error — server not running, connection refused, etc.
      // This catch block handles errors from fetch() itself (not HTTP errors).
      setEditMessage({ text: `[Error] Network error: ${error.message}`, type: 'error' })
      setTimeout(() => setEditMessage(null), 4000)
    }
  }

  // -------------------------------------------------------------------------
  // MERGE + SORT
  // -------------------------------------------------------------------------

  /**
   * Merge each player with their computed stats into a single object.
   *
   * This is necessary for two reasons:
   * 1. Rendering: Each table row needs to display both raw stats (from /players/)
   *    and computed stats (from /players/computed) in the same <tr>.
   * 2. Sorting: When the user clicks "Power Idx" header, we need each player
   *    object to have the power_index value so the sort comparator can access it.
   *
   * The spread operator (...) combines two objects into one:
   *   { ...player, ...(comp || {}) }
   * - ...player: all properties from the player object (id, name, team, stats)
   * - ...(comp || {}): all properties from computed stats (power_index, etc.)
   *   The || {} fallback handles the case where computed stats haven't loaded
   *   yet — spreading an empty object adds nothing (no crash).
   *
   * Since player and computed have different keys (except id, name, team),
   * there's no conflict. The computed values just get added alongside the raw stats.
   */
  // Merge each player with their computed stats into a single object.
  // In rolling mode, skip the merge since rolling data already contains
  // all the stats we need (computed directly from game log aggregation).
  // Rolling data uses player_id (MLB API ID) instead of id (DB auto-increment).
  const mergedPlayers = players.map((player) => {
    if (isRolling) return player  // Rolling data is already complete
    const comp = getComputedStats(player.id)
    // Merge fantasy points — same pattern as computed stats.
    // fantasyPoints is an array of {id, name, fantasy_pts} objects.
    // We find the matching entry by player ID and spread it into the player object.
    const fantasyPt = fantasyPoints?.find((f) => f.id === player.id)
    return { ...player, ...(comp || {}), ...(fantasyPt || {}) }
  })

  /**
   * Sort the merged player list based on the current sort state.
   *
   * Key concepts:
   * - .slice() creates a shallow copy so we don't mutate the original array.
   *   In React, you should NEVER mutate props directly — always work with copies.
   * - .sort() takes a comparator function: (a, b) => number
   *   - Return negative: a comes first
   *   - Return positive: b comes first
   *   - Return 0: order unchanged
   * - Null/undefined values are pushed to the end regardless of direction.
   *   This prevents "—" entries from cluttering the top of sorted results.
   * - String comparison uses localeCompare() for proper alphabetical sorting
   *   (handles accented characters like "Acuña" correctly).
   * - Numeric comparison uses simple subtraction (a - b).
   * - The direction multiplier (1 or -1) flips the sort order for descending.
   *
   * If sortColumn is null (no sort active), we skip sorting and use the
   * original mergedPlayers array directly.
   */
  const sortedPlayers = sortColumn
    ? mergedPlayers.slice().sort((a, b) => {
        const aVal = a[sortColumn]
        const bVal = b[sortColumn]

        // Push nulls/undefined to the end, regardless of sort direction.
        // This ensures missing data doesn't interfere with the sort.
        if (aVal == null && bVal == null) return 0
        if (aVal == null) return 1   // a has no value → push it down
        if (bVal == null) return -1  // b has no value → push it down

        // Direction multiplier: 1 for ascending, -1 for descending.
        // Multiplying the comparison result by -1 reverses the order.
        const direction = sortDirection === 'asc' ? 1 : -1

        // String comparison (for name, team, position columns).
        // localeCompare() handles international characters correctly.
        if (typeof aVal === 'string') {
          return direction * aVal.localeCompare(bVal)
        }

        // Numeric comparison (for all stat columns).
        // Subtraction gives: negative if a < b, positive if a > b, 0 if equal.
        return direction * (aVal - bVal)
      })
    : mergedPlayers  // No sort active — use original order from the API

  // -------------------------------------------------------------------------
  // PAGINATION
  // -------------------------------------------------------------------------
  // IMPORTANT: Sorting happens FIRST on ALL players, THEN we paginate.
  // This ensures when you sort by HR descending, you see the league leaders
  // on page 1, not just the top HR hitters from whatever 50 were on page 1.
  //
  // totalPages: Calculate how many pages we need (Math.ceil rounds up so
  //   745 players / 50 per page = 15 pages, not 14.9)
  // startIndex: First player index for current page (page 0 = index 0-49)
  // endIndex: Last player index (exclusive) for .slice()
  // paginatedPlayers: The 50 (or fewer) players to display on this page
  const totalPages = Math.ceil(sortedPlayers.length / PLAYERS_PER_PAGE)
  const startIndex = currentPage * PLAYERS_PER_PAGE
  const endIndex = startIndex + PLAYERS_PER_PAGE
  const paginatedPlayers = sortedPlayers.slice(startIndex, endIndex)

  // Pagination navigation handlers
  const goToFirstPage = () => setCurrentPage(0)
  const goToPrevPage = () => setCurrentPage((prev) => Math.max(0, prev - 1))
  const goToNextPage = () => setCurrentPage((prev) => Math.min(totalPages - 1, prev + 1))
  const goToLastPage = () => setCurrentPage(totalPages - 1)

  return (
    <div className="player-table">
      {/* Table title and position filter are now in the unified header bar
          rendered by App.jsx directly above this component. */}

      {/*
        Show edit feedback message above the table.
        Conditional rendering: editMessage && (...) only renders the div
        if editMessage is not null. The className dynamically switches between
        "form-message-success" (green) and "form-message-error" (red).
      */}
      {editMessage && (
        <div className={`form-message form-message-${editMessage.type}`}>
          {editMessage.text}
        </div>
      )}

      <table>
        <thead>
          <tr>
            {/*
              Render column headers from the COLUMNS definition array.
              Each <th> is clickable and shows a sort indicator.
              The className "sortable-th" adds cursor:pointer and hover styles.

              TOOLTIP IMPLEMENTATION:
              Each header contains a tooltip that appears on hover, showing
              how the stat is calculated. The tooltip uses CSS for positioning
              and visibility (see .column-tooltip in App.css).
              - The tooltip wrapper has position:relative so the tooltip can
                position itself absolutely relative to the header.
              - The tooltip text is in a <span> that's hidden by default and
                shown on hover via CSS :hover selector.
            */}
            {activeColumns.map((col) => (
              <th
                key={col.key}
                className={`sortable-th has-tooltip${col.key === 'name' ? ' sticky-name' : ''}${col.mobileHide ? ' mobile-hide' : ''}`}
                onClick={() => handleSort(col.key)}
              >
                <span className="column-header-content">
                  {col.label}
                  <span className="sort-indicator">{getSortIndicator(col.key)}</span>
                  {/* Tooltip: Shows on hover, explains what the stat means */}
                  {col.tooltip && (
                    <span className="column-tooltip">{col.tooltip}</span>
                  )}
                </span>
              </th>
            ))}
            {/*
              Actions column header — not sortable, doesn't have click handler.
              This column contains Edit/Save/Cancel buttons for each row.
              Hidden in rolling mode because rolling data is aggregated from
              game logs and can't be edited (it's read-only computed data).
            */}
            {!isRolling && <th>Actions</th>}
          </tr>
        </thead>
        <tbody>
          {/*
            Render paginatedPlayers (not sortedPlayers) — this is the subset
            of players for the current page AFTER sorting ALL players first.
          */}
          {paginatedPlayers.map((player, index) => {
            // Check if THIS specific row is in edit mode.
            // Only one row can be edited at a time (editingId is a single value).
            // In rolling mode, editing is disabled so isEditing is always false.
            const isEditing = !isRolling && editingId === player.id

            // Use player.id for season data, player.player_id for rolling data.
            // Rolling data has player_id (MLB API ID) instead of id (DB primary key).
            // Fall back to index if neither exists (shouldn't happen, but safe).
            const rowKey = player.id ?? player.player_id ?? index

            return (
              <tr key={rowKey}>
                {activeColumns.map((col) => {
                  // ---------------------------------------------------------
                  // EDIT MODE: Show input fields for editable columns
                  // ---------------------------------------------------------
                  // If this row is being edited AND this column is editable,
                  // render an <input> instead of plain text.
                  // Computed columns (isComputed: true) are never editable because
                  // they're derived from other stats by Polars on the backend.
                  if (isEditing && col.editable) {
                    return (
                      <td key={col.key} className={col.mobileHide ? 'mobile-hide' : undefined}>
                        <input
                          className="edit-input"
                          name={col.key}           // Matches the key in editForm state
                          type={col.inputType || 'text'}  // "number" or "text"
                          step={col.inputStep}     // e.g., "0.001" for decimal inputs
                          value={editForm[col.key] ?? ''}  // Controlled: value from state
                          onChange={handleEditChange}  // Update state on every keystroke
                        />
                      </td>
                    )
                  }

                  // ---------------------------------------------------------
                  // DISPLAY MODE: Show formatted text values
                  // ---------------------------------------------------------
                  // For non-editing rows (or non-editable columns in editing rows),
                  // display the value with optional formatting.
                  const rawValue = player[col.key]
                  const displayValue = col.formatRow
                    ? col.formatRow(player)      // Format using full player object (e.g., H/AB)
                    : col.format
                      ? col.format(rawValue)     // Apply format function if defined
                      : col.isComputed
                        ? (rawValue ?? '—')      // Computed: show dash if missing
                        : rawValue               // Raw: show as-is

                  return (
                    <td
                      key={col.key}
                      // Add "computed-stat" class for purple styling on computed columns
                      // Add "mobile-hide" class for columns hidden on small screens
                      // Add "sticky-name" class for the Name column so it stays visible while scrolling
                      className={[
                        col.isComputed ? 'computed-stat' : '',
                        col.mobileHide ? 'mobile-hide' : '',
                        col.key === 'name' ? 'sticky-name' : '',
                      ].filter(Boolean).join(' ') || undefined}
                    >
                      {/* Name column: render as a clickable link that opens the player detail modal.
                          Other columns render as plain text. */}
                      {col.key === 'name' && onPlayerClick ? (
                        <span
                          className="player-name-link"
                          onClick={() => onPlayerClick(player)}
                          role="button"
                          tabIndex={0}
                          onKeyDown={(e) => e.key === 'Enter' && onPlayerClick(player)}
                        >
                          {displayValue}
                        </span>
                      ) : (
                        displayValue
                      )}
                    </td>
                  )
                })}

                {/*
                  Actions cell: Shows different buttons based on edit state.
                  - Normal mode: "Edit" button to enter edit mode for this row.
                  - Edit mode: "Save" to submit changes, "Cancel" to discard.

                  The <> (Fragment) wrapper lets us render two sibling buttons
                  without adding an extra DOM element. It's shorthand for
                  <React.Fragment>.

                  Arrow functions in onClick (e.g., () => handleEditClick(player))
                  create a closure that captures the current player object.
                  This is necessary because handleEditClick needs to know WHICH
                  player was clicked — we can't pass arguments directly to onClick.
                */}
                {/* Actions cell: Hidden in rolling mode since aggregated data
                    from game logs is read-only and can't be edited. */}
                {!isRolling && (
                  <td className="actions-cell">
                    {isEditing ? (
                      <>
                        <button className="btn-save" onClick={() => handleSaveEdit(player)}>Save</button>
                        <button className="btn-cancel" onClick={handleCancelEdit}>Cancel</button>
                      </>
                    ) : (
                      <>
                        <button className="btn-edit" onClick={() => handleEditClick(player)}>Edit</button>
                        {onAddToComparison && (
                          <button
                            className={`btn-compare ${comparisonIds?.has(player.id) ? 'active' : ''}`}
                            onClick={() => onAddToComparison(player, 'batter')}
                            disabled={comparisonIds?.has(player.id)}
                            title={comparisonIds?.has(player.id) ? 'Already in comparison' : 'Add to comparison'}
                          >
                            {comparisonIds?.has(player.id) ? '✓' : 'Compare'}
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

      {/*
        Pagination Controls
        -------------------
        Shows: « First | ‹ Prev | Page X of Y (showing Z-W of N players) | Next › | Last »

        The controls are disabled when you're at the boundary:
        - First/Prev disabled on page 1
        - Next/Last disabled on the last page

        The info text shows the actual player range being displayed:
        - "Showing 1-50 of 745 players" on page 1
        - "Showing 51-100 of 745 players" on page 2
        - "Showing 701-745 of 745 players" on page 15 (last page, partial)
      */}
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
              (showing {startIndex + 1}-{Math.min(endIndex, sortedPlayers.length)} of {sortedPlayers.length} players)
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

export default PlayerTable
