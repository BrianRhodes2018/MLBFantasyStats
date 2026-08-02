const COLUMNS = [
  ['Projected Ks', 'The model’s average or expected strikeout total.'],
  ['Median', 'The middle result in the modeled strikeout distribution.'],
  ['80% interval', 'The model’s 10th-to-90th-percentile predictive range for one game.'],
  ['P(5+) / P(6+)', 'The estimated probability of at least five or at least six strikeouts.'],
  ['Projected BF', 'Projected batters faced, representing expected workload.'],
  ['Actual Ks', 'Strikeouts in the official final MLB pitching line.'],
  ['Error', 'Projected Ks minus Actual Ks. Positive means the model projected too many.'],
  ['Result', 'Final means the pitcher started and was graded; DNS means the projected pitcher did not start. Postponed, suspended, and pending games remain unresolved.'],
  ['Model spread', 'Highest projection minus lowest projection across the three approaches.'],
  ['Lineup / as of', 'The lineup source and the timestamp when inputs were frozen.'],
]

const TERMS = [
  ['Expected value', 'The long-run average outcome represented by Projected Ks.'],
  ['Prediction interval', 'A range for a future pitcher result, not a confidence interval for a fitted average.'],
  ['Calibration', 'Whether predicted probabilities and intervals occur at their stated rates over many forecasts.'],
  ['Chronological holdout', 'Later games withheld from training and used only for evaluation.'],
  ['Walk-forward validation', 'Repeated training on the past and testing on the next unseen time period.'],
  ['Backtest', 'Evaluation on historical, point-in-time data that was unavailable to the model during training.'],
  ['Live evaluation', 'Scoring projections that were actually published before their games began.'],
  ['MAE', 'Mean absolute error: the average distance between projected and actual Ks. Lower is better.'],
  ['RMSE', 'Root mean squared error: like MAE, but it penalizes large misses more heavily. Lower is better.'],
  ['Bias', 'Average signed error. Positive overprojects Ks; negative underprojects them.'],
  ['Interval coverage', 'The share of actual outcomes falling inside the published prediction interval.'],
  ['Brier score', 'Mean squared error for a probability forecast such as P(5+). Lower is better.'],
  ['K/BF', 'Strikeouts per batter faced, separating strikeout skill from workload.'],
  ['Count model', 'A model built specifically for non-negative totals such as strikeouts.'],
  ['Quantile model', 'A model that estimates parts of the outcome distribution instead of only its average.'],
  ['Beta-binomial', 'A distribution that allows uncertainty and extra variation in strikeout rate.'],
  ['Empirical Bayes / shrinkage', 'Moves small-sample rates toward stable league or group averages.'],
  ['Workload model', 'Estimates how many batters a pitcher is likely to face.'],
  ['Frozen cohort', 'The identical pitcher slate scored by all three approaches at the same as-of time.'],
  ['Point-in-time data', 'Only information that existed when the projection was generated.'],
  ['Data leakage', 'Accidentally allowing future or same-game outcomes into model inputs.'],
]


export default function PitcherKsLegend() {
  return (
    <details className="pitcher-ks-legend">
      <summary>
        <span>How to read this board</span>
        <small>Column definitions and data-science terms</small>
      </summary>
      <div className="pitcher-ks-legend-content">
        <section aria-labelledby="pitcher-k-column-legend">
          <h3 id="pitcher-k-column-legend">Column definitions</h3>
          <dl>
            {COLUMNS.map(([term, definition]) => (
              <div key={term}>
                <dt>{term}</dt>
                <dd>{definition}</dd>
              </div>
            ))}
          </dl>
        </section>
        <section aria-labelledby="pitcher-k-science-legend">
          <h3 id="pitcher-k-science-legend">Data-science terms</h3>
          <dl>
            {TERMS.map(([term, definition]) => (
              <div key={term}>
                <dt>{term}</dt>
                <dd>{definition}</dd>
              </div>
            ))}
          </dl>
        </section>
        <p className="pitcher-ks-interval-note">
          An 80% prediction interval does not mean the model is “80% correct.”
          Across many similarly produced projections, about 80% of actual results
          should fall inside the intervals when they are well calibrated.
        </p>
      </div>
    </details>
  )
}
