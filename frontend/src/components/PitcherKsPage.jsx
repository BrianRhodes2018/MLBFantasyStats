import { useEffect, useState } from 'react'

import { API_BASE } from '../config'
import { fetchData, formatPct } from '../utils/hitPicksUi'


const APPROACHES = {
  decomposed: {
    eyebrow: 'Approach 1 · decomposed model',
    title: 'Decomposed Matchup + Workload Simulation',
    subtitle: 'A learned K/BF component and a separate batters-faced model combined into one count distribution.',
    methodology: 'Gradient-boosted strikeout-rate and workload models are trained independently, then combined with a beta-binomial workload mixture. This keeps a short leash from being mistaken for weak strikeout skill.',
  },
  count: {
    eyebrow: 'Approach 2 · direct ML model',
    title: 'Direct Count + Quantile Model',
    subtitle: 'Poisson mean and quantile gradient boosting trained directly on starter-game strikeout totals.',
    methodology: 'One model predicts the expected count while two chronological quantile models estimate its conditional spread. Their output is converted into a coherent discrete strikeout distribution.',
  },
  bayes: {
    eyebrow: 'Approach 3 · transparent baseline',
    title: 'Empirical-Bayes Matchup Baseline',
    subtitle: 'Shrunk pitcher and opponent strikeout rates mixed over projected workload.',
    methodology: 'Pitcher, recent-form, projected-lineup, opponent-team, and league rates are regressed toward stable priors before being mixed over a batters-faced distribution.',
  },
}


function formatNumber(value, digits = 1) {
  return value === null || value === undefined ? '—' : Number(value).toFixed(digits)
}

function formatTimestamp(value) {
  if (!value) return 'As-of time unavailable'
  return new Intl.DateTimeFormat('en-US', {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
    timeZoneName: 'short',
  }).format(new Date(value))
}


export default function PitcherKsPage({ approach = 'decomposed' }) {
  const config = APPROACHES[approach] || APPROACHES.decomposed
  const [board, setBoard] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    fetchData(`${API_BASE}/api/pitcher-ks/approaches/${approach}/latest`)
      .then((data) => {
        if (!cancelled) {
          setBoard(data)
          setError(null)
        }
      })
      .catch((err) => {
        if (!cancelled) setError(err.message)
      })
    return () => { cancelled = true }
  }, [approach])

  const loading = !board || board.approach !== approach
  const metrics = board?.backtest || {}
  const predictions = loading ? [] : board?.predictions || []

  return (
    <main className="betting-page pitcher-ks-page">
      <header className="betting-header hit-picks-header pitcher-ks-header">
        <div>
          <span className="hit-history-eyebrow">{config.eyebrow}</span>
          <h2>{config.title}</h2>
          <p className="betting-subtitle">{config.subtitle}</p>
        </div>
        <span className="pitcher-ks-status-badge">
          {board?.model_version || 'Model loading'}
        </span>
      </header>

      <p className="betting-methodology">{config.methodology}</p>

      {error && (
        <div className="hit-history-error" role="alert">
          Could not load Pitcher Ks: {error}
        </div>
      )}

      <aside className="pitcher-ks-readiness" aria-label="Model validation summary">
        <span>Chronological validation</span>
        <strong>
          {metrics.starts
            ? `${metrics.starts.toLocaleString()} held-out starts · MAE ${formatNumber(metrics.mae, 2)} Ks`
            : 'Waiting for a published model run'}
        </strong>
        <p>
          {metrics.starts
            ? `RMSE ${formatNumber(metrics.rmse, 2)}, bias ${formatNumber(metrics.bias, 2)}, 80% interval coverage ${formatPct(metrics.interval_80_coverage)}. Trained through ${board.trained_through} on ${board.trained_on_rows.toLocaleString()} starter-games.`
            : 'The scorer publishes only complete frozen slates; it never runs a model inside this browser request.'}
        </p>
      </aside>

      <section className="pitcher-ks-board" aria-labelledby="pitcher-ks-board-title">
        <div className="pitcher-ks-board-heading">
          <div>
            <span className="hit-history-eyebrow">Daily projection board</span>
            <h3 id="pitcher-ks-board-title">
              {board ? `${board.projection_date} starting pitchers` : 'Today\'s starting pitchers'}
            </h3>
          </div>
          <span className="pitcher-ks-run-state">
            {loading ? 'Loading run' : `${predictions.length} pitchers`}
          </span>
        </div>

        <div className="hit-picks-table-wrap" aria-busy={loading}>
          <table className="audit-signal-table hit-picks-table pitcher-ks-table">
            <thead>
              <tr>
                <th>Pitcher</th>
                <th>Matchup</th>
                <th>Projected Ks</th>
                <th>Median</th>
                <th>80% interval</th>
                <th>P(5+)</th>
                <th>P(6+)</th>
                <th>Projected BF</th>
                <th>Lineup / as of</th>
              </tr>
            </thead>
            <tbody>
              {predictions.map((prediction) => (
                <tr key={`${prediction.game_pk}-${prediction.pitcher_id}`}>
                  <td>
                    <strong>{prediction.pitcher_name}</strong>
                    <small>{prediction.team} · {prediction.pitcher_throws || '?'}HP</small>
                  </td>
                  <td>
                    vs {prediction.opponent}
                    <small>{prediction.venue || 'Venue pending'}</small>
                  </td>
                  <td><strong>{formatNumber(prediction.projected_ks, 2)}</strong></td>
                  <td>{prediction.median_ks}</td>
                  <td>{prediction.p10_ks}–{prediction.p90_ks}</td>
                  <td>{formatPct(prediction.probability_5_plus)}</td>
                  <td>{formatPct(prediction.probability_6_plus)}</td>
                  <td>{formatNumber(prediction.projected_batters_faced, 1)}</td>
                  <td>
                    {prediction.lineup_source}
                    <small>{formatTimestamp(board.as_of_timestamp)}</small>
                  </td>
                </tr>
              ))}
              {!loading && !predictions.length && (
                <tr>
                  <td colSpan="9" className="pitcher-ks-empty-cell">
                    <div className="pitcher-ks-empty">
                      <h3>No pitcher projections available</h3>
                      <p>Run predict_pitcher_ks_today.py after training the model package.</p>
                    </div>
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
    </main>
  )
}
