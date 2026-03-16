/**
 * LeagueSelector.jsx - Fantasy League Manager (ESPN + Yahoo)
 * ===========================================================
 *
 * This component lets users connect their ESPN or Yahoo fantasy leagues
 * and switch between them. It provides:
 * - A dropdown to select which league's scoring rules to apply
 * - A "+ League" button to open an inline form for adding new leagues
 * - A provider toggle (ESPN / Yahoo) inside the add-league form
 * - A "- League" button to remove the currently selected league
 *
 * ESPN flow: Enter league ID + optional cookies → done.
 * Yahoo flow: 2-step wizard:
 *   Step 1: Enter Yahoo app credentials → get authorization link
 *   Step 2: Open link, get verification code, enter it + league key → connect
 *
 * When a league is selected, the app computes fantasy points for every player
 * using that league's scoring settings (fetched from ESPN/Yahoo Fantasy API).
 *
 * Data flow:
 *   LeagueSelector → POST /fantasy/leagues (add ESPN league)
 *                   → POST /fantasy/yahoo/auth-url (get Yahoo auth link)
 *                   → POST /fantasy/leagues (add Yahoo league w/ auth code)
 *                   → DELETE /fantasy/leagues/{id} (remove league)
 *                   → onLeagueChange callback (notify App.jsx of selection change)
 *                   → onLeagueAdded/onLeagueRemoved callbacks (trigger list refresh)
 *
 * Props:
 * @param {Array} leagues - Array of saved league objects from GET /fantasy/leagues
 * @param {number|null} activeLeagueId - Database ID of the currently selected league
 * @param {Function} onLeagueChange - Callback when user selects a different league
 * @param {Function} onLeagueAdded - Callback after a new league is successfully added
 * @param {Function} onLeagueRemoved - Callback after a league is removed
 */

import { useState } from 'react'

// API_BASE is the backend URL prefix. Empty string in dev (uses Vite proxy),
// full URL in production (e.g., "https://your-app.onrender.com").
import { API_BASE } from '../config'

function LeagueSelector({ leagues, activeLeagueId, onLeagueChange, onLeagueAdded, onLeagueRemoved }) {
  // --- State for the "Add League" inline form ---
  // showAddForm: controls whether the add-league form is visible
  // provider: "espn" or "yahoo" — determines which form to show
  const [showAddForm, setShowAddForm] = useState(false)
  const [provider, setProvider] = useState('espn')

  // --- ESPN-specific state ---
  const [leagueIdInput, setLeagueIdInput] = useState('')
  const [espnS2Input, setEspnS2Input] = useState('')
  const [swidInput, setSwidInput] = useState('')

  // --- Yahoo-specific state ---
  // yahooStep: 1 = enter credentials, 2 = enter verification code + league key
  const [yahooStep, setYahooStep] = useState(1)
  const [yahooConsumerKey, setYahooConsumerKey] = useState('')
  const [yahooConsumerSecret, setYahooConsumerSecret] = useState('')
  const [yahooAuthUrl, setYahooAuthUrl] = useState('')       // Generated auth URL
  const [yahooVerificationCode, setYahooVerificationCode] = useState('')
  const [yahooLeagueKey, setYahooLeagueKey] = useState('')

  // --- Shared state ---
  const [addMessage, setAddMessage] = useState(null)
  const [addLoading, setAddLoading] = useState(false)

  /**
   * Reset all form state when switching providers or closing the form.
   * This prevents stale data from one provider leaking into the other.
   */
  const resetForm = () => {
    // ESPN
    setLeagueIdInput('')
    setEspnS2Input('')
    setSwidInput('')
    // Yahoo
    setYahooStep(1)
    setYahooConsumerKey('')
    setYahooConsumerSecret('')
    setYahooAuthUrl('')
    setYahooVerificationCode('')
    setYahooLeagueKey('')
    // Shared
    setAddMessage(null)
    setAddLoading(false)
  }

  // =========================================================================
  // ESPN: Add League Handler
  // =========================================================================
  /**
   * Handle adding a new ESPN league — sends POST /fantasy/leagues
   * with the user's ESPN league ID and optional auth cookies.
   *
   * The backend then:
   * 1. Calls the ESPN Fantasy API to fetch the league's scoring settings
   * 2. Saves the league config to the database
   * 3. Returns the league name and scoring rules
   */
  const handleAddEspnLeague = async () => {
    if (!leagueIdInput.trim()) {
      setAddMessage({ text: 'Please enter an ESPN League ID', type: 'error' })
      return
    }

    setAddLoading(true)
    setAddMessage(null)

    try {
      const res = await fetch(`${API_BASE}/fantasy/leagues`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          provider: 'espn',
          league_id: parseInt(leagueIdInput, 10),
          // Send null (not empty string) for optional fields so the backend
          // knows they weren't provided. Empty strings would cause ESPN API errors.
          espn_s2: espnS2Input.trim() || null,
          swid: swidInput.trim() || null,
        }),
      })
      const data = await res.json()

      if (res.ok && data.code === 201) {
        setAddMessage({ text: data.message, type: 'success' })
        setShowAddForm(false)
        resetForm()
        if (onLeagueAdded) onLeagueAdded()
        setTimeout(() => setAddMessage(null), 4000)
      } else {
        setAddMessage({ text: data.message || 'Failed to add league', type: 'error' })
        setTimeout(() => setAddMessage(null), 6000)
      }
    } catch (err) {
      setAddMessage({ text: `Network error: ${err.message}`, type: 'error' })
      setTimeout(() => setAddMessage(null), 6000)
    } finally {
      setAddLoading(false)
    }
  }

  // =========================================================================
  // YAHOO STEP 1: Get Authorization URL
  // =========================================================================
  /**
   * Sends the Yahoo Consumer Key to the backend, which constructs an
   * authorization URL. The user opens this URL in their browser, logs
   * into Yahoo, and approves access. Yahoo then shows them a verification
   * code that they paste into Step 2.
   */
  const handleYahooGetAuthUrl = async () => {
    if (!yahooConsumerKey.trim()) {
      setAddMessage({ text: 'Please enter your Yahoo Consumer Key', type: 'error' })
      return
    }

    setAddLoading(true)
    setAddMessage(null)

    try {
      const res = await fetch(`${API_BASE}/fantasy/yahoo/auth-url`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          consumer_key: yahooConsumerKey.trim(),
        }),
      })
      const data = await res.json()

      if (res.ok && data.code === 200 && data.data?.auth_url) {
        // Store the auth URL and advance to Step 2
        setYahooAuthUrl(data.data.auth_url)
        setYahooStep(2)
        setAddMessage({ text: 'Authorization link generated! See Step 2 below.', type: 'success' })
        setTimeout(() => setAddMessage(null), 4000)
      } else {
        setAddMessage({ text: data.message || 'Failed to generate auth URL', type: 'error' })
        setTimeout(() => setAddMessage(null), 6000)
      }
    } catch (err) {
      setAddMessage({ text: `Network error: ${err.message}`, type: 'error' })
      setTimeout(() => setAddMessage(null), 6000)
    } finally {
      setAddLoading(false)
    }
  }

  // =========================================================================
  // YAHOO STEP 2: Exchange Code + Connect League
  // =========================================================================
  /**
   * Sends the verification code + league key + credentials to the backend.
   * The backend:
   * 1. Exchanges the code for OAuth access/refresh tokens
   * 2. Fetches the league's scoring settings from Yahoo's API
   * 3. Saves everything to the database
   * 4. Returns the league name and scoring rules
   */
  const handleYahooConnectLeague = async () => {
    if (!yahooVerificationCode.trim()) {
      setAddMessage({ text: 'Please enter the verification code from Yahoo', type: 'error' })
      return
    }
    if (!yahooLeagueKey.trim()) {
      setAddMessage({ text: 'Please enter your Yahoo League Key', type: 'error' })
      return
    }

    setAddLoading(true)
    setAddMessage(null)

    try {
      const res = await fetch(`${API_BASE}/fantasy/leagues`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          provider: 'yahoo',
          yahoo_league_key: yahooLeagueKey.trim(),
          yahoo_consumer_key: yahooConsumerKey.trim(),
          yahoo_consumer_secret: yahooConsumerSecret.trim(),
          yahoo_authorization_code: yahooVerificationCode.trim(),
        }),
      })
      const data = await res.json()

      if (res.ok && data.code === 201) {
        setAddMessage({ text: data.message, type: 'success' })
        setShowAddForm(false)
        resetForm()
        if (onLeagueAdded) onLeagueAdded()
        setTimeout(() => setAddMessage(null), 4000)
      } else {
        setAddMessage({ text: data.message || 'Failed to connect Yahoo league', type: 'error' })
        setTimeout(() => setAddMessage(null), 6000)
      }
    } catch (err) {
      setAddMessage({ text: `Network error: ${err.message}`, type: 'error' })
      setTimeout(() => setAddMessage(null), 6000)
    } finally {
      setAddLoading(false)
    }
  }

  // =========================================================================
  // Remove League Handler
  // =========================================================================
  /**
   * Handle removing the currently selected league.
   * Shows a confirmation dialog first, then sends DELETE /fantasy/leagues/{id}.
   * After removal, the dropdown goes back to "No League".
   */
  const handleRemoveLeague = async () => {
    if (!activeLeagueId) return

    const league = leagues.find(l => l.id === activeLeagueId)
    if (!league) return

    // Show the provider name in the confirmation so the user knows which type
    const providerName = league.provider === 'yahoo' ? 'Yahoo' : 'ESPN'
    const readdHint = league.provider === 'yahoo'
      ? 'You can always re-add it later by going through the Yahoo authorization flow again.'
      : 'You can always re-add it later with the same ESPN League ID.'

    const confirmed = window.confirm(
      `Are you sure you want to remove "${league.league_name}" (${providerName})?\n\n${readdHint}`
    )
    if (!confirmed) return

    try {
      const res = await fetch(`${API_BASE}/fantasy/leagues/${activeLeagueId}`, {
        method: 'DELETE',
      })
      const data = await res.json()

      if (res.ok) {
        if (onLeagueRemoved) onLeagueRemoved()
        setAddMessage({ text: `League "${league.league_name}" removed`, type: 'success' })
        setTimeout(() => setAddMessage(null), 3000)
      }
    } catch (err) {
      console.error('Failed to remove league:', err)
    }
  }

  return (
    <div className="league-selector" style={{ position: 'relative' }}>
      {/* ---- League Dropdown ---- */}
      {/* This dropdown lets the user pick which league's scoring to apply.
          "No League" means no fantasy points are computed/displayed. */}
      <div className="table-header-filter">
        <label htmlFor="league-select">League:</label>
        <select
          id="league-select"
          value={activeLeagueId || ''}
          onChange={(e) => onLeagueChange(
            e.target.value ? parseInt(e.target.value, 10) : null
          )}
        >
          <option value="">No League</option>
          {leagues.map(league => (
            <option key={league.id} value={league.id}>
              {league.league_name}
            </option>
          ))}
        </select>
      </div>

      {/* ---- Add/Remove Buttons ---- */}
      <button
        className="btn-add-league"
        onClick={() => {
          if (showAddForm) {
            setShowAddForm(false)
            resetForm()
          } else {
            setShowAddForm(true)
          }
        }}
        title="Add Fantasy League"
      >
        {showAddForm ? 'Cancel' : '+ League'}
      </button>

      {/* Only show the remove button when a league is actually selected */}
      {activeLeagueId && (
        <button
          className="btn-remove-league"
          onClick={handleRemoveLeague}
          title="Remove selected league"
        >
          − League
        </button>
      )}

      {/* ---- Add League Inline Form ---- */}
      {/* This form appears below the header bar when the user clicks "+ League".
          A provider toggle at the top switches between ESPN and Yahoo forms. */}
      {showAddForm && (
        <div className="add-league-form">
          {/* ---- Provider Toggle ---- */}
          {/* Two buttons side-by-side to pick ESPN or Yahoo.
              Styled like the time period selector buttons. */}
          <div className="provider-toggle">
            <button
              className={`provider-toggle-btn ${provider === 'espn' ? 'active' : ''}`}
              onClick={() => { setProvider('espn'); resetForm(); setProvider('espn'); }}
            >
              ESPN
            </button>
            <button
              className={`provider-toggle-btn ${provider === 'yahoo' ? 'active' : ''}`}
              onClick={() => { setProvider('yahoo'); resetForm(); setProvider('yahoo'); }}
            >
              Yahoo
            </button>
          </div>

          {/* ============================================================
              ESPN FORM — Same as before: league ID + optional cookies
              ============================================================ */}
          {provider === 'espn' && (
            <>
              <h4 style={{ margin: '0 0 8px 0', color: '#e0e8f0' }}>
                Connect ESPN Fantasy League
              </h4>

              {/* ESPN League ID — required field */}
              <div className="add-league-field">
                <label>ESPN League ID: <span style={{ color: '#e74c3c' }}>*</span></label>
                <input
                  type="number"
                  value={leagueIdInput}
                  onChange={(e) => setLeagueIdInput(e.target.value)}
                  placeholder="e.g., 12345"
                />
                <div className="field-help">
                  <strong>How to find it:</strong>
                  <ol>
                    <li>Go to your ESPN Fantasy Baseball league page</li>
                    <li>Look at the URL in your browser's address bar</li>
                    <li>Find the number after <code>leagueId=</code></li>
                  </ol>
                  <div className="field-help-example">
                    Example URL: fantasy.espn.com/baseball/league?leagueId=<strong>12345</strong>
                    <br />Your League ID would be <strong>12345</strong>
                  </div>
                </div>
              </div>

              {/* Collapsible section for private league cookies */}
              <details className="private-league-details">
                <summary>
                  Private league? Click here for cookie setup
                </summary>
                <div className="private-league-help">
                  <p>
                    If your league is <strong>private</strong>, you need two cookies from your browser.
                    <strong> Public leagues don't need these</strong> — just leave them blank.
                  </p>
                  <div className="field-help">
                    <strong>How to find your cookies (Edge):</strong>
                    <ol>
                      <li>Log into ESPN Fantasy in Edge</li>
                      <li>Press <code>F12</code> to open Developer Tools</li>
                      <li>Click the <strong>Application</strong> tab at the top</li>
                      <li>In the left sidebar, expand <strong>Cookies</strong></li>
                      <li>Click on <strong>https://www.espn.com</strong></li>
                      <li>Find <strong>espn_s2</strong> in the list — click on its row, then copy its <strong>Value</strong> from the bottom panel</li>
                      <li>Find <strong>SWID</strong> in the list — same process, copy its <strong>Value</strong></li>
                    </ol>
                    <small style={{ color: '#8899aa' }}>
                      Tip: Edge uses the same DevTools as Chrome since they're both Chromium-based.
                    </small>
                  </div>

                  <div className="field-help">
                    <strong>How to find your cookies (Chrome):</strong>
                    <ol>
                      <li>Log into ESPN Fantasy in Chrome</li>
                      <li>Press <code>F12</code> to open Developer Tools</li>
                      <li>Click the <strong>Application</strong> tab at the top</li>
                      <li>In the left sidebar, expand <strong>Cookies</strong></li>
                      <li>Click on <strong>https://www.espn.com</strong></li>
                      <li>Find <strong>espn_s2</strong> in the list — click on its row, then copy its <strong>Value</strong> from the bottom panel</li>
                      <li>Find <strong>SWID</strong> in the list — same process, copy its <strong>Value</strong></li>
                    </ol>
                  </div>

                  <div className="field-help">
                    <strong>How to find your cookies (Firefox):</strong>
                    <ol>
                      <li>Log into ESPN Fantasy in Firefox</li>
                      <li>Press <code>F12</code> to open Developer Tools</li>
                      <li>Click the <strong>Storage</strong> tab at the top</li>
                      <li>Expand <strong>Cookies</strong> in the left sidebar</li>
                      <li>Click on <strong>https://www.espn.com</strong></li>
                      <li>Find and copy the values for <strong>espn_s2</strong> and <strong>SWID</strong></li>
                    </ol>
                  </div>

                  {/* espn_s2 cookie input */}
                  <div className="add-league-field">
                    <label>espn_s2 cookie:</label>
                    <input
                      type="text"
                      value={espnS2Input}
                      onChange={(e) => setEspnS2Input(e.target.value)}
                      placeholder="Paste espn_s2 cookie value here"
                    />
                    <small style={{ color: '#8899aa', fontSize: '0.75rem' }}>
                      A long string (~300+ characters). It starts with something like "AEB..."
                    </small>
                  </div>

                  {/* SWID cookie input */}
                  <div className="add-league-field">
                    <label>SWID cookie:</label>
                    <input
                      type="text"
                      value={swidInput}
                      onChange={(e) => setSwidInput(e.target.value)}
                      placeholder="Paste SWID cookie value here"
                    />
                    <small style={{ color: '#8899aa', fontSize: '0.75rem' }}>
                      A shorter value in curly braces, like: {'{XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX}'}
                    </small>
                  </div>
                </div>
              </details>

              {/* ESPN action buttons */}
              <div className="add-league-actions">
                <button
                  onClick={handleAddEspnLeague}
                  disabled={addLoading}
                  className="btn-connect-league"
                >
                  {addLoading ? 'Connecting...' : 'Connect League'}
                </button>
                <button
                  onClick={() => { setShowAddForm(false); resetForm(); }}
                  className="btn-cancel-league"
                >
                  Cancel
                </button>
              </div>
            </>
          )}

          {/* ============================================================
              YAHOO FORM — 2-step wizard
              Step 1: Enter credentials → get authorization link
              Step 2: Enter verification code + league key → connect
              ============================================================ */}
          {provider === 'yahoo' && (
            <>
              <h4 style={{ margin: '0 0 8px 0', color: '#e0e8f0' }}>
                Connect Yahoo Fantasy League
              </h4>

              {/* Step indicator — shows which step the user is on */}
              <div className="yahoo-step-indicator">
                <div className={`yahoo-step ${yahooStep >= 1 ? 'active' : ''}`}>
                  <span className="yahoo-step-number">1</span>
                  <span className="yahoo-step-label">App Credentials</span>
                </div>
                <div className="yahoo-step-divider" />
                <div className={`yahoo-step ${yahooStep >= 2 ? 'active' : ''}`}>
                  <span className="yahoo-step-number">2</span>
                  <span className="yahoo-step-label">Authorize & Connect</span>
                </div>
              </div>

              {/* ---- STEP 1: Enter Yahoo App Credentials ---- */}
              {yahooStep === 1 && (
                <>
                  <div className="field-help" style={{ marginBottom: '10px' }}>
                    <strong>One-time setup — create a Yahoo Developer app:</strong>
                    <ol>
                      <li>Go to <code>developer.yahoo.com/apps</code></li>
                      <li>Click <strong>"Create an App"</strong></li>
                      <li>App Name: anything (e.g., "My Fantasy App")</li>
                      <li>App Type: select <strong>"Installed Application"</strong></li>
                      <li>API Permissions: check <strong>"Fantasy Sports"</strong> → <strong>Read</strong></li>
                      <li>Click <strong>"Create App"</strong></li>
                      <li>Copy the <strong>Client ID (Consumer Key)</strong> and <strong>Client Secret (Consumer Secret)</strong></li>
                    </ol>
                  </div>

                  {/* Consumer Key */}
                  <div className="add-league-field">
                    <label>Consumer Key (Client ID): <span style={{ color: '#e74c3c' }}>*</span></label>
                    <input
                      type="text"
                      value={yahooConsumerKey}
                      onChange={(e) => setYahooConsumerKey(e.target.value)}
                      placeholder="Paste your Yahoo Client ID here"
                    />
                  </div>

                  {/* Consumer Secret */}
                  <div className="add-league-field">
                    <label>Consumer Secret (Client Secret): <span style={{ color: '#e74c3c' }}>*</span></label>
                    <input
                      type="text"
                      value={yahooConsumerSecret}
                      onChange={(e) => setYahooConsumerSecret(e.target.value)}
                      placeholder="Paste your Yahoo Client Secret here"
                    />
                    <small style={{ color: '#8899aa', fontSize: '0.75rem' }}>
                      This stays on your computer and is only used to authenticate with Yahoo.
                    </small>
                  </div>

                  {/* Step 1 action button */}
                  <div className="add-league-actions">
                    <button
                      onClick={handleYahooGetAuthUrl}
                      disabled={addLoading || !yahooConsumerKey.trim()}
                      className="btn-connect-league"
                    >
                      {addLoading ? 'Generating...' : 'Get Authorization Link'}
                    </button>
                    <button
                      onClick={() => { setShowAddForm(false); resetForm(); }}
                      className="btn-cancel-league"
                    >
                      Cancel
                    </button>
                  </div>
                </>
              )}

              {/* ---- STEP 2: Authorize & Enter Verification Code ---- */}
              {yahooStep === 2 && (
                <>
                  {/* Authorization link — user opens this in a new tab */}
                  <div className="yahoo-auth-link-box">
                    <p style={{ margin: '0 0 8px 0', fontSize: '0.85rem', color: '#a0b0c0' }}>
                      Click the button below to open Yahoo in a new tab.
                      Log in, click <strong>"Agree"</strong>, then copy the
                      verification code Yahoo shows you.
                    </p>
                    <a
                      href={yahooAuthUrl}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="btn-yahoo-auth-link"
                    >
                      Open Yahoo Authorization Page
                    </a>
                  </div>

                  {/* Verification Code */}
                  <div className="add-league-field">
                    <label>Verification Code: <span style={{ color: '#e74c3c' }}>*</span></label>
                    <input
                      type="text"
                      value={yahooVerificationCode}
                      onChange={(e) => setYahooVerificationCode(e.target.value)}
                      placeholder="Paste the code Yahoo gave you"
                    />
                    <small style={{ color: '#8899aa', fontSize: '0.75rem' }}>
                      After clicking "Agree" on Yahoo, you'll see a code on the page. Copy and paste it here.
                    </small>
                  </div>

                  {/* Yahoo League Key */}
                  <div className="add-league-field">
                    <label>Yahoo League Key: <span style={{ color: '#e74c3c' }}>*</span></label>
                    <input
                      type="text"
                      value={yahooLeagueKey}
                      onChange={(e) => setYahooLeagueKey(e.target.value)}
                      placeholder="e.g., 458.l.123456"
                    />
                    <div className="field-help">
                      <strong>How to find your League Key:</strong>
                      <ol>
                        <li>Go to your Yahoo Fantasy Baseball league page</li>
                        <li>Look at the URL: <code>baseball.fantasysports.yahoo.com/b2/<strong>123456</strong></code></li>
                        <li>The number at the end is your league ID</li>
                        <li>Your full league key is: <strong>458.l.YOUR_NUMBER</strong></li>
                      </ol>
                      <div className="field-help-example">
                        Example: If your URL ends with <strong>/b2/54321</strong>
                        <br />Your League Key is <strong>458.l.54321</strong>
                        <br /><small>(458 = 2025 MLB season game ID)</small>
                      </div>
                    </div>
                  </div>

                  {/* Step 2 action buttons */}
                  <div className="add-league-actions">
                    <button
                      onClick={() => { setYahooStep(1); setAddMessage(null); }}
                      className="btn-cancel-league"
                    >
                      Back
                    </button>
                    <button
                      onClick={handleYahooConnectLeague}
                      disabled={addLoading || !yahooVerificationCode.trim() || !yahooLeagueKey.trim()}
                      className="btn-connect-league"
                    >
                      {addLoading ? 'Connecting...' : 'Connect League'}
                    </button>
                    <button
                      onClick={() => { setShowAddForm(false); resetForm(); }}
                      className="btn-cancel-league"
                    >
                      Cancel
                    </button>
                  </div>
                </>
              )}
            </>
          )}

          {/* ---- Inline Error/Success inside the form ---- */}
          {addMessage && showAddForm && (
            <div className={`league-message league-message-${addMessage.type}`}>
              {addMessage.text}
            </div>
          )}
        </div>
      )}

      {/* ---- Feedback Message (when form is closed) ---- */}
      {/* Shows success/error messages after adding or removing a league */}
      {addMessage && !showAddForm && (
        <div
          className={`league-message league-message-${addMessage.type}`}
          style={{
            position: 'absolute',
            top: '100%',
            left: 0,
            marginTop: '8px',
            zIndex: 99,
          }}
        >
          {addMessage.text}
        </div>
      )}
    </div>
  )
}

export default LeagueSelector
