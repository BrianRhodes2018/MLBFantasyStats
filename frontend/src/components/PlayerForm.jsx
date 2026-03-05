/**
 * PlayerForm.jsx - Add New Player Form Component
 * ================================================
 *
 * A controlled form that lets users add a new MLB player to the database
 * via the POST /players/ endpoint. Shows a confirmation message after
 * submission with the API's response code and message.
 *
 * Key React concepts demonstrated:
 * - Controlled components: Input values are driven by React state, not the DOM.
 *   Every keystroke updates state, and state drives what the input displays.
 *   This gives React full control over the form data.
 * - Event handling: onChange fires on every keystroke, onSubmit fires on form submit
 * - Spread operator (...): Used to merge objects (updating one field while keeping others)
 * - Callback props: The parent (App) passes a function that this component calls
 *   after successfully adding a player, triggering a data refresh
 * - setTimeout: Used to auto-clear the confirmation message after a delay
 * - Conditional rendering: The message only renders when state is non-null
 *
 * Props:
 * @param {Function} onPlayerAdded - Callback function to call after a player is
 *   successfully created. The parent (App.jsx) uses this to re-fetch all data.
 */

import { useState } from 'react'
import { API_BASE } from '../config'

/**
 * Initial form state — all fields start empty.
 * We define this as a constant OUTSIDE the component so it's created once
 * and reused every time we reset the form (not recreated on each render).
 * Now includes the optional 'position' field.
 */
const INITIAL_FORM = {
  name: '',
  team: '',
  position: '',
  batting_average: '',
  home_runs: '',
  rbi: '',
  stolen_bases: '',
  ops: '',
}

function PlayerForm({ onPlayerAdded }) {
  // State variable holding all form field values as a single object.
  // Using one state object (instead of separate useState calls) keeps
  // the code cleaner and makes reset easy — just set it back to INITIAL_FORM.
  const [form, setForm] = useState(INITIAL_FORM)
  const [isOpen, setIsOpen] = useState(false)

  // State for the confirmation/error message shown after form submission.
  // null = no message shown, object = { text, type } where type is "success" or "error".
  // This gives the user clear feedback about what happened — similar to
  // how Swagger UI shows the response code and message after a request.
  const [message, setMessage] = useState(null)

  /**
   * Handle changes to any input field.
   *
   * This is a single handler for ALL inputs, using the input's `name`
   * attribute to determine which field to update. This avoids writing
   * separate handler functions for each field.
   *
   * How it works:
   * 1. e.target.name gives us the field name (e.g., "home_runs")
   * 2. e.target.value gives us the new value the user typed
   * 3. ...form spreads the existing form values (keeps other fields unchanged)
   * 4. [e.target.name]: e.target.value updates just the one field that changed
   *
   * The [computed property] syntax uses the variable's VALUE as the key.
   * So if e.target.name is "home_runs", it becomes { home_runs: "52" }.
   *
   * @param {Event} e - The input change event
   */
  const handleChange = (e) => {
    setForm({ ...form, [e.target.name]: e.target.value })
  }

  /**
   * Show a temporary message that auto-clears after 4 seconds.
   *
   * setTimeout() schedules a function to run after a delay (in milliseconds).
   * We use it here to automatically hide the success/error message so
   * the user doesn't have to dismiss it manually.
   *
   * @param {string} text - The message text to display
   * @param {string} type - "success" or "error" (controls CSS styling)
   */
  const showMessage = (text, type) => {
    setMessage({ text, type })
    // Clear the message after 4 seconds (4000ms)
    setTimeout(() => setMessage(null), 4000)
  }

  /**
   * Handle form submission — POST the new player to the backend.
   *
   * Steps:
   * 1. e.preventDefault() stops the browser from reloading the page
   *    (default form behavior is to navigate to the action URL)
   * 2. Convert string values to numbers (inputs always give strings)
   * 3. Send a POST request with JSON body to /players/
   * 4. Parse the API response which includes code and message fields
   * 5. Show the response message to the user with appropriate styling
   *
   * The API now returns a Swagger-style response:
   *   { "code": 201, "message": "Player 'Aaron Judge' created successfully", "data": {...} }
   *
   * @param {Event} e - The form submit event
   */
  const handleSubmit = async (e) => {
    e.preventDefault()

    // Build the payload with proper types.
    // HTML inputs always return strings, but our API expects numbers
    // for the stat fields. parseFloat() handles decimals, parseInt()
    // handles whole numbers.
    const payload = {
      name: form.name,
      team: form.team,
      position: form.position || null,    // Send null if position is empty string
      batting_average: parseFloat(form.batting_average),
      home_runs: parseInt(form.home_runs, 10),    // 10 = base-10 (decimal)
      rbi: parseInt(form.rbi, 10),
      stolen_bases: parseInt(form.stolen_bases, 10),
      ops: parseFloat(form.ops),
    }

    try {
      // fetch() with method: 'POST' sends data to the server.
      // headers tell the server we're sending JSON.
      // body is the JSON-stringified payload.
      const res = await fetch(`${API_BASE}/players/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })

      // Parse the JSON response body. The backend now returns ApiResponse
      // with code, message, and data fields — just like Swagger UI shows.
      const responseData = await res.json()

      if (res.ok) {
        // res.ok is true for HTTP 200-299 status codes.
        // Show the API's message (e.g., "Player 'Aaron Judge' created successfully")
        // along with the response code (e.g., 201).
        showMessage(`[${responseData.code}] ${responseData.message}`, 'success')
        // Reset the form back to empty fields.
        setForm(INITIAL_FORM)
        // Notify the parent that a new player was added so it can refresh data.
        onPlayerAdded()
      } else {
        // HTTP error (4xx or 5xx). Show the error message from the API.
        // FastAPI validation errors (422) include a "detail" field.
        const errorMsg = responseData.message || responseData.detail || 'Failed to add player'
        showMessage(`[${res.status}] ${errorMsg}`, 'error')
      }
    } catch (error) {
      // Network error (server not running, no internet, etc.)
      showMessage(`[Error] Network error: ${error.message}`, 'error')
    }
  }

  return (
    <div className="player-form">
      <h2
        className="collapsible-heading"
        onClick={() => setIsOpen(!isOpen)}
      >
        <span className="collapse-indicator">{isOpen ? '▾' : '▸'}</span>
        Add New Player
      </h2>

      {isOpen && (
        <>
          {message && (
            <div className={`form-message form-message-${message.type}`}>
              {message.text}
            </div>
          )}

          <form onSubmit={handleSubmit}>
            <input
              name="name"
              placeholder="Player Name"
              value={form.name}
              onChange={handleChange}
              required
            />
            <input
              name="team"
              placeholder="Team"
              value={form.team}
              onChange={handleChange}
              required
            />
            <input
              name="position"
              placeholder="Position (e.g. RF, DH)"
              value={form.position}
              onChange={handleChange}
            />
            <input
              name="batting_average"
              placeholder="Batting Avg"
              type="number"
              step="0.001"
              value={form.batting_average}
              onChange={handleChange}
              required
            />
            <input
              name="home_runs"
              placeholder="Home Runs"
              type="number"
              value={form.home_runs}
              onChange={handleChange}
              required
            />
            <input
              name="rbi"
              placeholder="RBI"
              type="number"
              value={form.rbi}
              onChange={handleChange}
              required
            />
            <input
              name="stolen_bases"
              placeholder="Stolen Bases"
              type="number"
              value={form.stolen_bases}
              onChange={handleChange}
              required
            />
            <input
              name="ops"
              placeholder="OPS"
              type="number"
              step="0.001"
              value={form.ops}
              onChange={handleChange}
              required
            />
            <button type="submit">Add Player</button>
          </form>
        </>
      )}
    </div>
  )
}

export default PlayerForm
