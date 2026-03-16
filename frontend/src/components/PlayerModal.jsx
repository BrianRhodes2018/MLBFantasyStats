/**
 * PlayerModal.jsx - Player Detail Modal with News, Fantasy Blurbs & Transactions
 * ================================================================================
 *
 * A modal overlay that appears when a user clicks a player name in
 * PlayerTable or PitcherTable. It displays:
 *
 * 1. Player headshot (from MLB's image CDN using the player's mlb_id)
 * 2. Game log stats (last 10 games with opponent, date, per-game stats)
 * 3. ESPN news articles (player-specific, from ESPN athlete overview API)
 * 4. RotoWire fantasy blurb (delivered via the same ESPN overview endpoint)
 * 5. MLB transaction history (trades, IL stints, callups, etc.)
 *
 * Data sources (backend proxied):
 * - Headshot: https://img.mlbstatic.com/mlb-photos/image/upload/.../people/{mlb_id}/headshot/...
 * - Game Logs: GET /player-detail/gamelogs/batter/{mlb_id} or /pitcher/{mlb_id}
 *   (queries local DB — batter_game_logs / pitcher_game_logs tables)
 * - ESPN News + RotoWire: GET /player-detail/news?name={name}&mlb_id={mlb_id}
 *   (single call returns both articles[] and rotowire{} from ESPN's athlete overview)
 * - Transactions: GET /player-detail/transactions/{mlb_id}
 *
 * Props:
 * - player: Object — the full player/pitcher data object from the table row.
 *           Must have `name`, `team`, and optionally `position`, `mlb_id`.
 * - playerType: String — "batter" or "pitcher" (for context display).
 * - onClose: Function — callback to close the modal (resets parent state).
 */

import { useState, useEffect } from 'react'
import { API_BASE } from '../config'

function PlayerModal({ player, playerType, onClose }) {
  // -----------------------------------------------------------------------
  // State
  // -----------------------------------------------------------------------
  // null = loading, [] = loaded but empty, [...] = loaded with data
  const [news, setNews] = useState(null)
  const [rotowire, setRotowire] = useState(null)     // null = loading, object = blurb, false = none
  const [transactions, setTransactions] = useState(null)
  const [gameLogs, setGameLogs] = useState(null)
  const [playerAge, setPlayerAge] = useState(null)
  const [newsError, setNewsError] = useState(null)
  const [transactionsError, setTransactionsError] = useState(null)
  const [gameLogsError, setGameLogsError] = useState(null)
  const [activeTab, setActiveTab] = useState('gamelog')

  // -----------------------------------------------------------------------
  // Construct the MLB headshot URL
  // -----------------------------------------------------------------------
  // Uses img.mlbstatic.com (Cloudinary CDN) which is the correct domain.
  // The "d_people:generic:headshot:67:current.png" parameter provides a
  // generic silhouette fallback if the player's photo isn't available.
  const headshotUrl = player.mlb_id
    ? `https://img.mlbstatic.com/mlb-photos/image/upload/d_people:generic:headshot:67:current.png/w_213,q_auto:best/v1/people/${player.mlb_id}/headshot/67/current`
    : null

  // -----------------------------------------------------------------------
  // Fetch ESPN news + RotoWire blurb (single call) and MLB transactions
  // -----------------------------------------------------------------------
  useEffect(() => {
    if (!player.mlb_id) return

    const controller = new AbortController()

    // The news endpoint returns both ESPN articles AND the RotoWire blurb
    // from ESPN's athlete overview API — one network call for both tabs.
    const fetchNewsAndRotowire = async () => {
      try {
        const params = new URLSearchParams({
          name: player.name,
          mlb_id: player.mlb_id,
        })
        const res = await fetch(
          `${API_BASE}/player-detail/news?${params}`,
          { signal: controller.signal }
        )
        const data = await res.json()
        if (!controller.signal.aborted) {
          setNews(data.data?.articles || [])
          // rotowire is either an object {headline, story, published} or null
          setRotowire(data.data?.rotowire || false)
        }
      } catch (err) {
        if (!controller.signal.aborted && err.name !== 'AbortError') {
          setNewsError('Failed to load news')
          setRotowire(false)
        }
      }
    }

    const fetchTransactions = async () => {
      try {
        const res = await fetch(
          `${API_BASE}/player-detail/transactions/${player.mlb_id}`,
          { signal: controller.signal }
        )
        const data = await res.json()
        if (!controller.signal.aborted) {
          setTransactions(data.data || [])
        }
      } catch (err) {
        if (!controller.signal.aborted && err.name !== 'AbortError') {
          setTransactionsError('Failed to load transactions')
        }
      }
    }

    const fetchGameLogs = async () => {
      try {
        // Use batter or pitcher endpoint based on playerType
        const type = playerType === 'pitcher' ? 'pitcher' : 'batter'
        const res = await fetch(
          `${API_BASE}/player-detail/gamelogs/${type}/${player.mlb_id}`,
          { signal: controller.signal }
        )
        const data = await res.json()
        if (!controller.signal.aborted) {
          // Response shape: data.data = { age: number, games: [] }
          const payload = data.data || {}
          setGameLogs(payload.games || [])
          if (payload.age != null) setPlayerAge(payload.age)
        }
      } catch (err) {
        if (!controller.signal.aborted && err.name !== 'AbortError') {
          setGameLogsError('Failed to load game logs')
        }
      }
    }

    fetchNewsAndRotowire()
    fetchTransactions()
    fetchGameLogs()

    return () => controller.abort()
  }, [player.mlb_id, player.name, playerType])

  // -----------------------------------------------------------------------
  // Close on Escape key
  // -----------------------------------------------------------------------
  useEffect(() => {
    const handleKeyDown = (e) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [onClose])

  // -----------------------------------------------------------------------
  // Lock body scroll while modal is open
  // -----------------------------------------------------------------------
  useEffect(() => {
    document.body.style.overflow = 'hidden'
    return () => {
      document.body.style.overflow = ''
    }
  }, [])

  // -----------------------------------------------------------------------
  // Helper: format a date string for display
  // -----------------------------------------------------------------------
  const formatDate = (dateStr) => {
    if (!dateStr) return ''
    try {
      const date = new Date(dateStr)
      return date.toLocaleDateString('en-US', {
        month: 'short',
        day: 'numeric',
        year: 'numeric',
      })
    } catch {
      return dateStr
    }
  }

  // -----------------------------------------------------------------------
  // Helper: format game log date as compact "Mar 11" format
  // -----------------------------------------------------------------------
  const formatShortDate = (dateStr) => {
    if (!dateStr) return ''
    try {
      // game_date is "YYYY-MM-DD" — add T00:00 to avoid timezone shift
      const date = new Date(dateStr + 'T00:00:00')
      return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
    } catch {
      return dateStr
    }
  }

  // -----------------------------------------------------------------------
  // Helper: truncate long text
  // -----------------------------------------------------------------------
  const truncate = (text, maxLen = 150) => {
    if (!text || text.length <= maxLen) return text
    return text.slice(0, maxLen).trimEnd() + '...'
  }

  // -----------------------------------------------------------------------
  // Render
  // -----------------------------------------------------------------------
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-content" onClick={(e) => e.stopPropagation()}>

        {/* Close button */}
        <button
          className="modal-close"
          onClick={onClose}
          aria-label="Close player detail modal"
        >
          &times;
        </button>

        {/* ---- Player Header: headshot + name/team/position ---- */}
        <div className="modal-player-header">
          {headshotUrl && (
            <img
              className="modal-headshot"
              src={headshotUrl}
              alt={`${player.name} headshot`}
              onError={(e) => {
                e.target.style.display = 'none'
              }}
            />
          )}
          <div className="modal-player-info">
            <h2 className="modal-player-name">{player.name}</h2>
            <p className="modal-player-meta">
              {player.team}{player.position ? ` | ${player.position}` : ''}
              {playerType ? ` | ${playerType === 'batter' ? 'Batter' : 'Pitcher'}` : ''}
              {playerAge != null ? ` | Age: ${playerAge}` : ''}
            </p>
          </div>
        </div>

        {/* ---- No MLB ID fallback ---- */}
        {!player.mlb_id ? (
          <div className="modal-no-data">
            <p>No MLB ID available for this player. News and transaction data cannot be loaded.</p>
          </div>
        ) : (
          <>
            {/* ---- Tab Navigation ---- */}
            <div className="modal-tabs">
              <button
                className={`modal-tab ${activeTab === 'gamelog' ? 'active' : ''}`}
                onClick={() => setActiveTab('gamelog')}
              >
                Game Log
              </button>
              <button
                className={`modal-tab ${activeTab === 'news' ? 'active' : ''}`}
                onClick={() => setActiveTab('news')}
              >
                ESPN News
              </button>
              <button
                className={`modal-tab ${activeTab === 'rotowire' ? 'active' : ''}`}
                onClick={() => setActiveTab('rotowire')}
              >
                Fantasy Blurbs
              </button>
              <button
                className={`modal-tab ${activeTab === 'transactions' ? 'active' : ''}`}
                onClick={() => setActiveTab('transactions')}
              >
                Transactions
              </button>
            </div>

            {/* ---- Tab Content ---- */}
            <div className="modal-tab-content">

              {/* --- Game Log Tab --- */}
              {activeTab === 'gamelog' && (
                <>
                  {gameLogsError && (
                    <div className="modal-error">{gameLogsError}</div>
                  )}

                  {!gameLogsError && gameLogs === null && (
                    <div className="modal-loading">
                      <div className="modal-loading-spinner"></div>
                      <div>Loading game logs...</div>
                    </div>
                  )}

                  {!gameLogsError && gameLogs !== null && gameLogs.length === 0 && (
                    <div className="modal-no-data">
                      No game log data available for this player.
                    </div>
                  )}

                  {!gameLogsError && gameLogs !== null && gameLogs.length > 0 && (
                    <div className="modal-gamelog-wrapper">
                      <table className="modal-gamelog-table">
                        <thead>
                          {playerType === 'pitcher' ? (
                            <tr>
                              <th>Date</th>
                              <th>Opp</th>
                              <th>IP</th>
                              <th>H</th>
                              <th>ER</th>
                              <th>BB</th>
                              <th>K</th>
                              <th>HR</th>
                              <th>W</th>
                              <th>L</th>
                              <th>SV</th>
                            </tr>
                          ) : (
                            <tr>
                              <th>Date</th>
                              <th>Opp</th>
                              <th>H/AB</th>
                              <th>HR</th>
                              <th>RBI</th>
                              <th>R</th>
                              <th>SB</th>
                              <th>BB</th>
                              <th>K</th>
                            </tr>
                          )}
                        </thead>
                        <tbody>
                          {gameLogs.map((game, idx) => (
                            playerType === 'pitcher' ? (
                              <tr key={idx}>
                                <td className="modal-gamelog-date">{formatShortDate(game.game_date)}</td>
                                <td className="modal-gamelog-opponent">{game.opponent}</td>
                                <td>{game.innings_pitched}</td>
                                <td>{game.hits_allowed}</td>
                                <td>{game.earned_runs}</td>
                                <td>{game.walks}</td>
                                <td>{game.strikeouts}</td>
                                <td>{game.home_runs_allowed}</td>
                                <td>{game.wins}</td>
                                <td>{game.losses}</td>
                                <td>{game.saves}</td>
                              </tr>
                            ) : (
                              <tr key={idx}>
                                <td className="modal-gamelog-date">{formatShortDate(game.game_date)}</td>
                                <td className="modal-gamelog-opponent">{game.opponent}</td>
                                <td>{game.hits}/{game.at_bats}</td>
                                <td>{game.home_runs}</td>
                                <td>{game.rbi}</td>
                                <td>{game.runs}</td>
                                <td>{game.stolen_bases}</td>
                                <td>{game.walks}</td>
                                <td>{game.strikeouts}</td>
                              </tr>
                            )
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </>
              )}

              {/* --- ESPN News Tab --- */}
              {activeTab === 'news' && (
                <>
                  {newsError && (
                    <div className="modal-error">{newsError}</div>
                  )}

                  {!newsError && news === null && (
                    <div className="modal-loading">
                      <div className="modal-loading-spinner"></div>
                      <div>Loading news...</div>
                    </div>
                  )}

                  {!newsError && news !== null && news.length === 0 && (
                    <div className="modal-no-data">
                      No recent ESPN news available for this player.
                    </div>
                  )}

                  {!newsError && news !== null && news.length > 0 && (
                    news.map((article, idx) => (
                      <div key={idx} className="modal-news-item">
                        <a
                          className="modal-news-headline"
                          href={article.link}
                          target="_blank"
                          rel="noopener noreferrer"
                        >
                          {article.headline}
                        </a>
                        <div className="modal-news-date">
                          {formatDate(article.published)}
                        </div>
                        {article.description && (
                          <div className="modal-news-desc">
                            {truncate(article.description)}
                          </div>
                        )}
                      </div>
                    ))
                  )}
                </>
              )}

              {/* --- RotoWire Fantasy Blurbs Tab --- */}
              {activeTab === 'rotowire' && (
                <>
                  {newsError && (
                    <div className="modal-error">{newsError}</div>
                  )}

                  {/* Loading state — rotowire is null until the news fetch completes */}
                  {!newsError && rotowire === null && (
                    <div className="modal-loading">
                      <div className="modal-loading-spinner"></div>
                      <div>Loading fantasy blurbs...</div>
                    </div>
                  )}

                  {/* No blurb available */}
                  {!newsError && rotowire === false && (
                    <div className="modal-no-data">
                      No RotoWire fantasy blurb available for this player.
                    </div>
                  )}

                  {/* Blurb found — show the full RotoWire content */}
                  {!newsError && rotowire && rotowire.headline && (
                    <div className="modal-rotowire-blurb">
                      <div className="modal-rotowire-headline">
                        {rotowire.headline}
                      </div>
                      <div className="modal-news-date">
                        {formatDate(rotowire.published)}
                      </div>
                      {rotowire.story && (
                        <div className="modal-rotowire-story">
                          {rotowire.story}
                        </div>
                      )}
                      <div className="modal-rotowire-credit">
                        Powered by RotoWire via ESPN
                      </div>
                    </div>
                  )}
                </>
              )}

              {/* --- Transactions Tab --- */}
              {activeTab === 'transactions' && (
                <>
                  {transactionsError && (
                    <div className="modal-error">{transactionsError}</div>
                  )}

                  {!transactionsError && transactions === null && (
                    <div className="modal-loading">
                      <div className="modal-loading-spinner"></div>
                      <div>Loading transactions...</div>
                    </div>
                  )}

                  {!transactionsError && transactions !== null && transactions.length === 0 && (
                    <div className="modal-no-data">
                      No transactions found in the past year.
                    </div>
                  )}

                  {!transactionsError && transactions !== null && transactions.length > 0 && (
                    transactions.map((txn, idx) => (
                      <div key={idx} className="modal-transaction-item">
                        <div className="modal-transaction-date">
                          {formatDate(txn.date)}
                        </div>
                        <div>
                          {txn.type && (
                            <div className="modal-transaction-type">
                              {txn.type}
                            </div>
                          )}
                          <div className="modal-transaction-desc">
                            {txn.description}
                          </div>
                        </div>
                      </div>
                    ))
                  )}
                </>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  )
}

export default PlayerModal
