# Plan: Hit Picks V3 Matchup and Opportunity Model

**Status:** Ready for Phase 2 feature development (Phase 0 and E1 complete)
**Created:** 2026-07-28
**Depends on:** [PLAN_ML_ENGINEERING_REMEDIATION.md](PLAN_ML_ENGINEERING_REMEDIATION.md)

## Goal

Build and validate a V3 model that improves the probability that a projected or confirmed starter records at least one hit, while keeping V2 live and available as the production fallback.

V3 should answer two questions more explicitly than V2:

1. How many plate appearances is the hitter likely to receive?
2. How likely is the hitter to record a hit in each expected plate appearance against the starter and bullpen?

The final product remains a reader-friendly game probability:

```text
Probability that the player records at least one hit
```

## Decision summary

- Keep `hit_gbm_v2_cal` as the production champion until V3 passes every promotion gate.
- Build V3 as a visible experimental challenger that scores the same daily
  slate without replacing V2 as the primary ranking.
- Test feature groups one at a time. Do not combine every new idea into one unreviewable model change.
- Add pitch-mix information as trained model features, not as a hand-written percentage bonus.
- Correct point-in-time, projected-lineup, park-factor, and calibration evaluation issues before claiming an improvement.
- Evaluate both an enriched game-level classifier and a decomposed opportunity/per-plate-appearance approach.
- Keep same-team stack probability as a separate simulation layer. Individual probabilities alone do not model joint outcomes.

---

## V2 baseline to preserve

The production baseline is defined in:

- `backend/train_hit_model.py`
- `backend/predict_hits_today.py`
- `backend/hit_calibration.py`
- `backend/calibration/hit_gbm_v2_isotonic.json`

Current behavior:

- Binary label: `got_hit`, where `1` means at least one hit and `0` means no hit.
- Model: `HistGradientBoostingClassifier`.
- Training data: historical 2023-2025 batter-games plus the current 2026 season through the day before prediction.
- Latest reviewed daily run: 153,623 training rows on 2026-07-27.
- Inputs: 51 pregame features covering batting order, home/away, platoon, BvP, park, batter form, starter quality, bullpen quality, and one strikeout interaction.
- Probability layer: isotonic calibration fitted from walk-forward base-model predictions.
- Product ranking: top 5, 10, and 15 hitters by calibrated probability.
- Frozen calibration test: 42,876 later batter-games from 2025-07-01 through
  2026-07-03, with Brier improving from 0.23555 raw to 0.23548 calibrated
  and a 71.82% top-10 hit rate.

Recorded evaluation evidence to retain as the comparison baseline:

- 72.2% pooled top-10 hit rate across the recorded 2026 walk-forward blocks.
- 71.1% top-10 hit rate over roughly 1,000 picks when trained on 2023-2025 and tested on the saved 2026 holdout.
- 67.5% top-10 hit rate for the naive season-rate benchmark in that holdout.
- Live results must remain separate from backtest results and be reported with sample sizes.

This reproduction is complete. The reviewable summary is
`backend/reports/v3/v2_benchmark_summary.json`; its row-level predictions and
daily top-N selections are retained locally with hashes recorded in that
summary.

---

## Non-goals

V3 does not initially include:

- Sportsbook price collection or expected-value betting recommendations.
- A manual rule such as `base probability + 5% for a good pitch matchup`.
- Same-team two-player or three-player joint probabilities.
- Treating the visible experimental V3 page as the primary recommendation
  before the new measurements have passed validation.
- Player or pitcher identity memorization through raw IDs.
- Training from completed-game information that was unavailable when the daily prediction would have run.
- Replacing V2 solely because V3 wins one month or one top-N metric.

---

## Primary product and statistical metrics

### Product metrics

- Top-5 hit rate by day.
- Top-10 hit rate by day.
- Top-15 hit rate by day.
- Number and percentage of picks from confirmed lineups.
- Number and percentage of picks from projected lineups.
- Starting-player and batting-slot projection accuracy.

### Probability metrics

- Brier score: average squared error of the probabilities.
- Log loss: penalizes confident incorrect probabilities.
- ROC AUC: measures ranking quality across all candidates.
- Calibration table: predicted probability compared with actual hit rate.
- Expected calibration error, with bucket counts retained.

### Operational metrics

- Candidate coverage by slate.
- Missing-data rate by feature and source.
- End-to-end daily runtime.
- Data-source failures and fallback usage.
- Percentage of predictions reproducible from a saved run manifest.

No single metric is sufficient. Top-N hit rate measures the visible product, while Brier score, log loss, and calibration measure whether the displayed percentages are trustworthy.

---

## V3 readiness foundation

Completed before feature development:

- [x] Persist the complete scored candidate slate while reader APIs still
  return only the requested top N.
- [x] Separate morning and afternoon evaluation windows and require a shared
  comparison group, cohort, and as-of snapshot.
- [x] Separate primary/challenger role, reader visibility, evaluation
  designation, and probability status.
- [x] Couple calibration to the model recipe, feature schema, and dependency
  environment; evaluate calibration on a later untouched block.
- [x] Provide visible V2, experimental V3, and strict comparison routes with
  safe empty states before V3 data exists.
- [x] Add partial uniqueness constraints, primary-first failure isolation, a
  bounded scheduler runtime, and paired-run/UI regression fixtures.

---

## Phase 0 - Freeze the experiment contract

### 0.1 Lock the run, game, and cohort identities

Before producing a V3 score:

- Persist every prediction as an immutable `run_id`; never overwrite a prior
  date/model run.
- Record `as_of_timestamp`, `prediction_mode`, model/runtime provenance, and
  the full candidate-cohort manifest on the run.
- Use `model_role` (`primary` or `challenger`) independently from
  `is_visible`. Use `is_evaluation` as the one date/model/window run allowed
  into that window's live ledger. Moving a pointer must not alter predictions.
- Record `comparison_group_id` and `prediction_window` (`morning` or
  `afternoon`) and require equal cohort, as-of time, and window before pairing.
- Carry MLB `game_pk` from candidate construction through JSON, database
  storage, API output, and grading.
- Grade by `(game_pk, player_id)`, not `(date, player_id)`, so doubleheaders
  remain separate and unfinished games on a partial slate remain pending.
- Build the candidate cohort once from game, player, lineup slot/source, and
  expected starter identity. V2 and V3 must match its SHA-256 id exactly.
- Fail the paired comparison if either model drops a candidate because a V3
  feature is missing. Missing optional features require an explicit fallback,
  not a different eligible population.

Legacy rows without `game_pk` may remain visible, but ambiguous doubleheader
rows must not be guessed or recomputed as if the two games were one.

### 0.2 Create a reproducible V2 benchmark

- Run V2 in the clean, locked environment.
- Save the exact training datasets, row counts, date bounds, feature order, model parameters, and calibration file hash.
- Save predictions for every evaluation row, not only aggregate metrics.
- Save the daily top-5, top-10, and top-15 selections.
- Record the code commit and dependency-lock hash.
- Run V2 twice and confirm identical predictions or document a justified numeric tolerance.

### 0.3 Predeclare evaluation periods

Use strictly chronological folds. A proposed layout is:

1. Train through 2023; test early and late 2024 blocks.
2. Train through each 2024 cutoff; test early and late 2025 blocks.
3. Train through 2025; test 2026 blocks.
4. Reserve the most recent complete block as the untouched final test.

The final dates must be written into the experiment configuration before V3 results are inspected.

### 0.4 Define uncertainty reporting

- Bootstrap by game date, not by individual player row.
- Report 95% confidence intervals for top-N rates and metric differences.
- Calculate the minimum detectable improvement for the available number of daily picks.
- Do not describe a small point-estimate increase as an improvement when the uncertainty includes a meaningful decline.

**Exit criteria:**

- V2 reproduces successfully in the locked environment.
- Prediction reruns are retained as separate runs, while the ledger counts
  only the designated evaluation run.
- The V2/V3 paired comparison rejects mismatched candidate-cohort ids.
- Doubleheader fixtures grade each game independently.
- The fold definitions and untouched final test are committed.
- The benchmark report includes predictions, metrics, confidence intervals, and provenance.

**Completed 2026-07-30:**

- [x] Frozen folds, the hidden V3 final block, metrics, bootstrap seed, and
  promotion gates in
  `backend/config/hit_model_v3_experiment.json` before any V3 result existed.
- [x] Produced 106,867 out-of-sample V2 predictions over 470 game dates and
  retained every daily top-5/10/15 selection.
- [x] Repeated the full package twice with identical artifact hashes and
  determinism key.
- [x] Measured V2 top-10 at 71.62% (3,366/4,700), with a date-clustered 95%
  interval of 70.26%-72.94%.
- [x] Predeclared an approximate 1.92 percentage-point minimum detectable
  top-10 improvement at 80% power for the current baseline sample.

---

## Phase 1 - Repair point-in-time and train/serve fidelity

These corrections precede new features so V3 is compared against an honest baseline.

### 1.1 Lineup-state fidelity

Current historical rows use the final box-score batting order, while live morning predictions may use projected lineups.

Add evaluation modes:

- `official`: score only after an official lineup was available.
- `projected`: recreate the lineup projection using only earlier games.
- `hybrid`: use official lineups when available and projections otherwise, matching production.

For every scored player, retain:

- `lineup_source`
- projected starter probability
- projected batting slot
- final starter status
- final batting slot
- timestamp or prediction window

Report model results separately for official and projected lineups.

### 1.2 Point-in-time park factors

- Store park-factor snapshots by effective date or season.
- Use only a park factor that would have been available before the historical game.
- Do not apply the current 2024-2026 rolling value to earlier dates during strict backtests.
- Retain a neutral fallback and a source indicator.

### 1.3 Point-in-time feature contract

Every feature builder must accept:

```text
game_date
as_of_timestamp
prediction_mode
```

Add a feature-schema manifest containing:

- feature name
- definition
- unit
- lookback window
- source
- missing-data behavior
- earliest supported date
- whether it is available for projected and official runs

### 1.4 Leakage tests

Add tests proving:

- Same-day results cannot enter a pregame feature.
- Later-season park factors cannot enter earlier rows.
- Official batting slots are not used in projected-mode backtests.
- Calibration fitting rows cannot enter the calibration test block.

**Exit criteria:**

- V2-corrected is evaluated separately from the original V2.
- Any change in reported V2 performance caused by more honest data timing is documented before V3 features are judged.

**Completed E1 evidence, 2026-07-30:**

- [x] Added separate historical `official` and `projected` builders. Projected
  lineups use only the previous 14 days and the same production formula;
  same-day final slots cannot enter the projection.
- [x] Retained lineup source, projected starter probability/slot, final
  starter/slot, probable-pitcher source, and zero-PA outcomes.
- [x] Added prior-season Baseball Savant park snapshots with effective dates,
  a neutral unavailable-venue fallback, and source metadata.
- [x] Used the archived probable pitcher for E1 features, with an explicit
  final-starter fallback.
- [x] Evaluated point-in-time official E1 separately: top-10 71.02%
  (95% CI 69.72%-72.32%).
- [x] Evaluated point-in-time projected E1 separately: top-10 68.15%
  (95% CI 66.71%-69.51%). Only 76.75% of projected candidates started and
  15.83% received zero plate appearances.
- [x] Documented hybrid evaluation as prospective-only: final game feeds do
  not retain the historical timestamp when a lineup was posted. Immutable
  live morning/afternoon snapshots will measure hybrid behavior honestly.

The corrected projected E1, not the original final-lineup-conditioned V2
number, is the baseline for morning V3 feature experiments.

---

## Phase 2 - Build new feature groups

Each feature group must be independently switchable for ablation testing.

Historical coverage was measured across 8,916 cached regular-season games in
`backend/reports/v3/feature_coverage.md`:

- Pitch type, velocity, movement, contact/batted-ball outcomes, starter
  workload, and bullpen workload are available from the existing MLB StatsAPI
  cache at approximately 99.4%-100% field coverage.
- xBA and bat-tracking fields are not present in that cache. They require a
  separately versioned Baseball Savant ingestion path and must retain missing
  indicators, sample counts, and cohort-preserving fallbacks.

### 2.1 Opportunity and expected plate appearances

Candidate features:

- Batting slot.
- Home/away.
- Confirmed versus projected lineup.
- Lineup confidence.
- Team offensive strength.
- Opposing starter quality.
- Expected team batters faced.
- Probability the home team does not bat in the ninth inning.
- Recent team plate appearances per game.
- Game run environment.

Train an opportunity model to estimate:

```text
P(PA = 0), P(PA = 1), P(PA = 2), P(PA = 3),
P(PA = 4), P(PA = 5), P(PA >= 6)
```

Do not use the eventual game plate-appearance count as an input.

The 0-2 buckets are required. They represent scratches, late substitutions,
pinch-hit-only appearances, shortened opportunity, and other real outcomes
that reduce the chance of recording a hit. If the opportunity model is
instead conditioned on "confirmed starter," that condition must be explicit
and the separate starter probability must be multiplied back into the final
game probability.

### 2.2 Batter contact and hit-quality features

Prioritize measurements aligned with becoming a hit:

- Season and recent xBA.
- xBA versus pitcher hand.
- Contact rate.
- Zone-contact rate.
- Whiff rate.
- Chase rate.
- Squared-up rate.
- Sprint Speed.
- BABIP with sample size.
- Ground-ball, line-drive, fly-ball, pull, center, and opposite-field rates when available point-in-time.

Use bat-speed and barrel features as secondary inputs, not as substitutes for xBA and contact skill.

Bat-tracking coverage begins during the second half of 2023. Every bat-tracking feature must include:

- sample count
- missing indicator
- fallback
- earliest supported date

### 2.3 Pitcher arsenal and batter pitch-type matchup

For each pitcher and batter hand, create:

- Pitch-type usage.
- Pitch velocity.
- Horizontal and vertical movement.
- Extension.
- Whiff rate.
- Contact rate allowed.
- xBA allowed.
- Location tendencies, if coverage is reliable.

For each batter, create corresponding performance against:

- pitch family
- velocity band
- movement/shape cluster
- pitcher hand

Initial aggregate matchup features:

```text
arsenal_match_xba
arsenal_match_contact
arsenal_match_whiff
arsenal_match_squared_up
arsenal_coverage
```

An aggregate should weight batter skill by the pitcher's expected usage:

```text
arsenal_match = sum(pitcher_usage[pitch] * shrunk_batter_skill[pitch])
```

Use shrinkage for small samples:

```text
shrunk_rate =
    weight * player_rate
    + (1 - weight) * league_rate

weight = sample_size / (sample_size + shrinkage_strength)
```

Tune `shrinkage_strength` only inside training folds.

Avoid raw pitch names as the only representation. Two sliders with different velocity and movement can be materially different pitches.

### 2.4 Starter workload and expected bullpen exposure

Add:

- Recent pitch counts.
- Days of rest.
- Recent innings and batters faced.
- Expected starter innings or batters faced.
- Opener/bulk-reliever status.
- Times-through-the-order tendency.
- Manager removal tendency, if it can be built without leakage.

Estimate how much of the hitter's game probability belongs to:

- first and second matchup against the starter
- possible third matchup against the starter
- expected bullpen plate appearances

### 2.5 Bullpen availability

Extend season bullpen quality with:

- pitches thrown yesterday
- pitches thrown over the last three days
- number of available relievers
- likely left/right reliever mix
- availability of the best relievers
- bullpen xBA allowed
- bullpen pitch-mix profile

### 2.6 Weather and park environment

After choosing a source with historical coverage, add:

- temperature
- wind speed and direction
- humidity
- precipitation risk
- roof status
- air-density proxy

Weather is a lower-priority experiment than opportunity, xBA/contact, and pitch mix.

### 2.7 Rework weak or redundant V2 inputs

Current saved datasets show:

- Only about 15.1% of rows have any prior BvP result.
- Only about 5.4% have at least five prior BvP plate appearances.
- Only about 0.44% have at least ten.
- Last-10 and last-20 hit rates are highly correlated.
- Season and last-20 hit rates are highly correlated.

Experiments:

- Remove raw BvP rate.
- Replace BvP with a shrunk estimate and explicit sample size.
- Replace overlapping last-5/10/20 rates with exponentially weighted form, season baseline, and recent-minus-season trend.
- Re-run group ablations before removing any original group permanently.

**Exit criteria for every feature group:**

- Historical and daily builders share one definition.
- Point-in-time tests pass.
- Coverage and missingness reports exist by season.
- The group can be enabled or disabled through experiment configuration.
- No feature depends on future data or final-game information.

---

## Phase 3 - Run a controlled model ladder

Do not change the algorithm and all features simultaneously.

### Experiment ladder

| ID | Candidate | Purpose |
|---|---|---|
| E0 | Original V2 reproduced | Locked benchmark |
| E1 | V2 with point-in-time and lineup fixes | Honest corrected baseline |
| E2 | E1 plus opportunity features | Tests plate-appearance value |
| E3 | E2 plus batter xBA/contact features | Tests underlying hitter skill |
| E4 | E3 plus pitch-arsenal matchup | Tests the proposed matchup improvement |
| E5 | E4 plus starter workload and bullpen availability | Tests full-game opponent exposure |
| E6 | E5 plus weather | Tests lower-priority environment value |
| E7 | Decomposed opportunity/per-PA architecture | Tests structural model change |

For E0-E6, keep `HistGradientBoostingClassifier` as the primary algorithm so feature value can be isolated.

After the best feature set is identified, compare:

- HistGradientBoostingClassifier
- regularized logistic regression
- one additional tree implementation only if it offers a clear operational or accuracy advantage

Hyperparameter selection must use time-aware inner folds. The untouched final test cannot guide tuning.

### Decomposed E7 model

The decomposed candidate calculates:

```text
P(at least one hit)
    = 1 - P(no hits across all expected plate appearances)
```

For a fixed number of appearances:

```text
P(at least one hit)
    = 1 - product(1 - per_PA_hit_probability[j])
```

When plate appearances are uncertain:

```text
P(at least one hit)
    = sum over n [
        P(PA = n)
        * P(at least one hit | PA = n)
      ]
```

The per-PA model should distinguish expected starter and bullpen exposure. It must be evaluated against the simpler enriched game-level classifier; greater complexity is not automatically better.

---

## Phase 4 - Calibrate probabilities correctly

Every model candidate that reaches final evaluation receives its own calibration.

Use three chronological layers:

1. Train the base model on earlier data.
2. Fit calibration on later out-of-sample base predictions.
3. Evaluate the calibrated probabilities on a still-later untouched block.

Compare:

- uncalibrated probabilities
- isotonic calibration
- sigmoid/Platt calibration

Choose the method using only calibration-training folds. Save:

- calibrator method
- fitting dates
- base-model fingerprint
- feature-schema hash
- dependency-lock hash
- Brier score and calibration table on the untouched test

A calibrator cannot be reused automatically after the model, features, or dependency environment changes.

---

## Phase 5 - Evaluation and promotion gates

### Required reports

- Overall candidate metrics by fold and pooled.
- Top-5/10/15 results by fold and pooled.
- Confirmed-lineup versus projected-lineup results.
- Probability calibration by bucket.
- Metrics by season, month, batting slot, pitcher hand, park type, and data-coverage tier.
- Feature-group ablation results.
- Missing-feature fallback results.
- Daily-clustered confidence intervals.
- Runtime and failure-rate comparison with V2.

### Promotion criteria

V3 can enter shadow production only if:

- All leakage and point-in-time tests pass.
- It improves the corrected V2 top-10 point estimate on the pooled test.
- Improvement is not concentrated in one fold or one short date range.
- It does not materially degrade Brier score or log loss.
- Calibration remains credible in the probability range shown to users.
- Projected-lineup performance is separately acceptable.
- Missing Statcast or weather data does not prevent a daily slate from scoring.
- Daily runtime stays within the scheduled operating window.

V3 can replace V2 publicly only if:

- Shadow predictions have run successfully for at least 30 calendar days.
- No unexplained data gaps or probability shifts remain.
- Live results are directionally consistent with the historical test, with uncertainty reported.
- The exact model/calibrator/environment bundle can be reproduced.
- V2 remains available for immediate rollback.

V3 may remain reader-visible during this period, but it must retain the
experimental label and must not be described as the primary recommendation.

The Phase 0 power analysis should define the practical improvement threshold. Do not invent a fixed percentage-point requirement after seeing the results.

---

## Phase 6 - Shadow deployment and product presentation

### Backend

- Score V2 and V3 from the same slate and lineup snapshot.
- Materialize the candidate cohort once and require both models to match its
  id before either result enters the comparison.
- Persist every scored candidate for both immutable runs without changing
  the primary model role.
- Store a run manifest, as-of timestamp, prediction mode, and cohort manifest
  for both models.
- Record feature coverage, fallback use, and lineup source.
- Serve the visible V2, visible experimental V3, and strict paired comparison
  endpoints. Return an explicit no-paired-run state instead of comparing
  different snapshots.

Suggested output fields:

```json
{
  "player_id": 123,
  "v2_probability": 0.664,
  "v3_probability": 0.687,
  "v3_delta": 0.023,
  "lineup_source": "official",
  "arsenal_coverage": 0.91,
  "feature_fallbacks": []
}
```

### Frontend

Three reader-visible routes are available during evaluation:

- `/hit-picks/v2` keeps the existing V2 board as primary.
- `/hit-picks/v3` uses the same board layout with prominent experimental
  labeling and calls uncalibrated output a model score.
- `/hit-picks/compare` shows rank movement, score changes, feature
  coverage/fallbacks, and actual statlines only for strictly paired runs.

Presentation rules:

- Existing V2 probability remains primary.
- Show pitcher arsenal, batter pitch-type strengths, coverage, and sample confidence.
- Label V3 as experimental.
- Never imply that a pitch-match score is itself a probability.

After promotion:

- Display one calibrated V3 probability.
- Keep matchup details explanatory, not additive.
- Continue showing model version and live graded sample size.

---

## Phase 7 - Same-team stack model, after V3

This is a separate deliverable.

Use V3 marginal player probabilities as inputs to a game simulation that also models:

- shared starter and bullpen exposure
- team plate-appearance environment
- lineup order
- game run environment
- home ninth-inning risk
- common weather and park conditions

The stack model should output:

- independence baseline
- simulated joint probability
- correlation adjustment
- uncertainty interval

Do not estimate same-team joint probability by arbitrarily increasing the product of individual probabilities.

---

## Proposed code and artifact layout

```text
backend/
  hit_model/
    __init__.py
    cohort.py
    config.py
    feature_schema.py
    opportunity.py
    plate_appearance.py
    game_probability.py
    calibration.py
    evaluation.py
    provenance.py
  data_sources/
    statcast.py
    weather.py
  experiments/
    hit_v3_experiments.yaml
  tests/
    test_hit_v3_features.py
    test_hit_v3_point_in_time.py
    test_hit_v3_opportunity.py
    test_hit_v3_probability.py
    test_hit_v3_calibration.py
    test_hit_v3_shadow.py
```

Large datasets, trained models, and prediction outputs remain outside Git. Small schemas, experiment configurations, calibration tables, and summary reports may be committed when reviewable.

---

## Test matrix

### Unit tests

- Shrinkage behavior at zero, small, and large samples.
- Arsenal-weighted feature math.
- Plate-appearance probability sums to one.
- Plate-appearance labels cover 0, 1, 2, 3, 4, 5, and 6+.
- `P(1+ hits | PA=0)` is exactly zero.
- At-least-one-hit conversion.
- Missing-data fallbacks.
- Weather and park neutral fallbacks.
- Model/calibrator fingerprint matching.

### Point-in-time tests

- No same-day outcomes in features.
- No future lineup, park, weather, or Statcast snapshot.
- No calibration overlap with calibration test dates.
- No final plate appearances in opportunity features.

### Integration tests

- Rebuild one historical date from raw inputs.
- Score the same date in official, projected, and hybrid modes.
- Train and score a small deterministic fixture.
- Run a full daily shadow generation without database writes.
- Persist both model versions as immutable runs; retrying the same `run_id`
  is idempotent.
- Reject a V2/V3 pair whose candidate-cohort ids differ.
- Grade a same-player doubleheader fixture by `game_pk`.

### Regression tests

- V2 fixture probabilities remain within the declared tolerance.
- Ranking is stable for a frozen dataset and environment.
- Calibrator rejects a mismatched base-model fingerprint.
- Missing optional sources still produce candidates.

---

## Rollout and rollback

### Rollout

1. [x] Complete the engineering-remediation plan.
2. [x] Freeze and reproduce V2.
3. [x] Correct point-in-time evaluation.
4. Build feature groups.
5. Run the experiment ladder.
6. Fit and test candidate-specific calibration.
7. Run V3 as a visible experimental challenger.
8. Review the promotion report.
9. Promote through a configuration switch.

### Rollback

- Keep V2 code, calibration, and environment bundle intact.
- Use a single configuration setting to select the public model.
- If V3 data coverage, runtime, or calibration monitoring fails, publish V2 for that run.
- Do not overwrite V2 ledger history with V3 results.
- Preserve the V3 run manifest for diagnosis.

---

## Open decisions to resolve before implementation

- Historical xBA and bat-tracking acquisition/caching policy. Existing cached
  MLB pitch events are sufficient for the initial pitch-mix, contact, starter,
  and bullpen experiments.
- Weather provider and historical coverage.
- Whether market-implied team totals are allowed as features or reserved for later betting-edge analysis.
- Definition of a likely available reliever.
- Minimum pitch-type sample before player-specific skill receives material weight.
- Whether movement-shape clustering belongs in initial V3 or a later V3.1.
- Storage location and retention policy for pitch-level data and trained artifacts.

---

## Definition of done

V3 is complete when:

- The model and calibrator are reproducible from a locked environment and run manifest.
- All features are point-in-time correct and shared between historical and daily scoring.
- The experiment ladder identifies which feature groups earned inclusion.
- The final untouched test and confidence intervals support promotion.
- Probabilities are independently calibrated and understandable.
- Shadow mode operates for the required period without unexplained failures.
- V2 remains a tested, immediate fallback.
- The public page clearly distinguishes probability, evidence, sample confidence, and model version.
