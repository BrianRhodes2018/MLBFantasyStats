# Plan: Pitcher_Ks Daily Strikeout Projections

**Status:** V1 implemented and populated locally on 2026-08-01
**Created:** 2026-08-01
**Proposed feature name:** `Pitcher_Ks`
**Recommended Python/package name:** `pitcher_ks`
**Depends on:** the existing schedule, game-feed cache, projected-lineup,
point-in-time feature, model-environment, and immutable-run infrastructure

## V1 implementation result (2026-08-01)

The repository now contains all three approaches, their chronological
backtest, an immutable paired run store, read-only API routes, and populated
frontend boards. The first daily run scored 30 probable starters for
2026-08-01 and stored 90 prediction rows (30 per approach).

Real-data validation used 17,906 starter-games from 2023-03-30 through
2026-07-31. The dataset contained 700 pitchers, zero duplicate
`game_pk`/pitcher identities, zero invalid targets, and no missing values in
the 20-feature model matrix. The 2025-2026 chronological holdouts contained
8,172 starts:

| Approach | MAE | RMSE | Bias | Mean log score | Nominal 80% interval coverage |
|---|---:|---:|---:|---:|---:|
| Decomposed K/BF + workload | 1.8329 | 2.2849 | +0.1131 | 2.2212 | 86.60% |
| Direct count + quantile | 1.8192 | 2.2750 | +0.0665 | 2.2346 | 85.22% |
| Empirical Bayes | 1.8722 | 2.3290 | +0.1316 | 2.2460 | 88.84% |

The direct model currently has the best held-out MAE/RMSE, while the
decomposed model has the best held-out distribution log score. All three
intervals are conservative relative to their nominal 80% target; that is
displayed as measured validation rather than hidden. The next calibration
iteration should narrow the intervals using later out-of-sample predictions,
without changing the already-frozen V1 results.

## Goal

For every expected MLB starting pitcher on a given day, produce a
point-in-time projection of his strikeout total and an honest probability
distribution around that projection.

The reader-facing answer should be more useful than a single rounded number:

```text
Pitcher: Example Starter
Projected Ks: 6.2
Median: 6
80% interval: 3-9
P(5+): 72%
P(6+): 55%
P(7+): 37%
Projected batters faced: 23.8
Lineup: projected (7 of 9 high-confidence)
As of: 2026-08-01 11:00 ET
```

The key modeling principle is:

```text
Total strikeouts = strikeout probability per batter faced
                 x opportunities to face batters
                 + game-level uncertainty
```

A high strikeout rate alone is not enough. A starter can miss the projection
because of a short pitch-count limit, inefficient innings, an early hook, an
opener role, a difficult lineup, a rain delay, or a starter scratch. The model
must represent both strikeout skill and workload.

## Decision summary

- Build an ML-backed feature with chronological backtesting. Do not ship a
  hand-tuned score as if it were a measured projection.
- Develop the approaches in increasing order of complexity: an empirical-Bayes
  benchmark, a direct count model, then the recommended decomposed simulation.
- Recommend the **decomposed plate-appearance plus workload model** as the
  likely production destination because it can return a coherent full K
  distribution and makes the two biggest sources of uncertainty explicit.
- Keep a direct gradient-boosted count model as a challenger. It is fast to
  build with the repository's locked scikit-learn stack and may prove just as
  accurate.
- Keep the empirical-Bayes model permanently as a transparent baseline and
  production fallback.
- Build one point-in-time dataset and score the same frozen slate with all
  model candidates. Model comparisons are invalid if they use different
  starters, lineups, or as-of timestamps.
- Evaluate morning/projected-lineup and afternoon/confirmed-lineup runs
  separately. Do not let confirmed historical lineups leak into a model meant
  to run before lineups post.
- Treat sportsbook lines, if added later, as an external benchmark first. A
  market-assisted model must be labeled separately from an independent baseball
  model.
- Do not promote a candidate based only on average error. It must also produce
  calibrated tail probabilities and reliable intervals.

## Scope

### Initial scope

- MLB regular-season games.
- Expected starting pitchers and explicitly identified bulk pitchers.
- Morning and afternoon projection windows.
- Point projection, median, full probability mass function, useful threshold
  probabilities, uncertainty interval, and data-quality metadata.
- Historical audit and daily grading.
- Fantasy/research presentation; no automatic wagering recommendation.

### Non-goals for the first release

- Relief-pitcher strikeout props.
- Predicting the exact pitch sequence thrown in a live game.
- Live in-game updating after first pitch.
- Copying proprietary Steamer, ZiPS, PECOTA, or THE BAT model weights.
- Claiming value against a sportsbook without time-stamped odds, no-vig
  probabilities, closing-line comparison, and a separately frozen evaluation.
- Using raw pitcher or batter IDs as unrestricted tree features. Identity may
  be used for historical aggregation or a controlled hierarchical effect, but
  the model should learn portable skills and context.

## What the repository already provides

This feature should reuse the existing foundations rather than start another
parallel data system.

| Existing component | Reuse for `Pitcher_Ks` |
|---|---|
| `backend/build_hit_dataset.py` | Chronological game replay, shared MLB game-feed cache, exact `game_pk`, boxscore outcomes, batter/pitcher history, point-in-time feature construction |
| `backend/models.py` pitcher and pitcher-game-log tables | Season totals, per-game Ks, exact innings, pitches, opponent, rest, and rolling-start inputs |
| `backend/projected_lineups.py` | Same projected/confirmed batting order and confidence used elsewhere in the product |
| `backend/hit_model/point_in_time.py` | Historical lineup projection using only prior games and prior-season park snapshots |
| `backend/hit_model/v3_features.py` | Batter/pitcher whiff and contact rates, pitch family, pitch usage, velocity, horizontal/vertical movement, extension, arsenal matching, and starter workload |
| `backend/park_factors.py` | Live and point-in-time park context with neutral fallback |
| `backend/hit_model/experiment_contract.py` | Frozen chronological folds, locked final test, run fingerprints, promotion gates, and fail-closed validation pattern |
| `backend/hit_model/benchmark.py` | Paired out-of-sample predictions, date-clustered uncertainty, artifact hashes, and reproducibility manifests |
| `backend/ml_environment.py` and `backend/uv.lock` | Locked Windows/Linux scientific runtime and provenance fingerprints |
| `backend/betting_math.py` | Later conversion of a model threshold probability and two-way odds to a no-vig comparison |
| Hit Picks immutable run/store pattern | Append-only prediction runs, publication pointer, morning/afternoon separation, grading, and API history |

Important gaps remain:

- Historical starter-level dataset with one row per `(game_pk, pitcher_id)`.
- A pregame workload target and model for batters faced/removal.
- Called-strike, zone, chase, first-pitch-strike, and put-away aggregates.
- Point-in-time catcher, umpire, weather, injury/pitch-limit, and market data.
- A pitcher projection run/store/API schema.
- A distributional grader rather than only an actual-K backfill.

## Free-data and integration audit (completed 2026-08-01)

### Conclusion

All three approaches can be developed through the K0-K4 feature ladder with
**no paid data feed, no paid model API, and no new ML library**. The core can
use data paths and packages already present in this repository. K5-K7 remain
optional because point-in-time umpire, injury/pitch-limit, weather licensing,
and sportsbook coverage are not equally painless or guaranteed.

| Need | Free source/path | Current integration | Audit result |
|---|---|---|---|
| Daily schedule, `game_pk`, probable starters, handedness | Public MLB StatsAPI/Gameday feeds | Used by `predict_hits_today.py`, matchups, and the game cache | Ready; no new provider |
| Final K, BF, outs/IP, pitches, and opposing lineup | MLB boxscore/live game feed | Used by pitcher game logs and `HitDatasetBuilder` | Ready; extend the historical row shape |
| Morning projected lineup | Recency-weighted projection from prior MLB boxscores | Shared `weighted_lineup_projection` fallback | Ready; SportsDataIO must stay optional |
| Confirmed lineup | MLB live game feed batting order | Existing uncached afternoon refresh | Ready |
| Batter/pitcher K rates and workload | Existing game logs and cached boxscores | Existing database and V2/V3 feature histories | Ready |
| Whiff, contact, pitch family, velocity, movement, extension, and arsenal fit | Existing cached pitch events; Baseball Savant fields are publicly documented | Existing V3 feature history | Ready through K4; add only the K-specific aggregates |
| Park context | Baseball Savant plus static neutral fallback | Existing `park_factors.py` and point-in-time snapshots | Ready; K-specific park rate is optional |
| Empirical Bayes, Poisson/count ML, quantiles, PMFs, and simulation | Installed NumPy, SciPy, and scikit-learn | Locked by `backend/uv.lock` | Ready; no new runtime dependency |
| Weather | Open-Meteo provides no-key forecast/history access for non-commercial use | Not integrated | Optional only; commercial use needs an appropriate licence or self-hosted/open alternative |
| Catcher framing and ABS context | Public Baseball Savant leaderboards/documentation | Not integrated | Optional; audit historical point-in-time coverage before K5 |
| Pregame umpire, injury, and explicit pitch-limit reports | No complete, dependable point-in-time free archive has been established | Not integrated | Exclude from MVP; missing cannot block a score |
| Sportsbook K lines/odds | No dependable free production feed has been established | Empty generic odds schema only | External benchmark later; never required by the core models |

References for the free/public rich-data boundary:

- [Baseball Savant Statcast CSV documentation](https://baseballsavant.mlb.com/csv-docs)
- [Baseball Savant catcher-framing leaderboard](https://baseballsavant.mlb.com/leaderboard/catcher-framing)
- [Open-Meteo free/open-source overview](https://open-meteo.com/)
- [Open-Meteo pricing and non-commercial free-tier limits](https://open-meteo.com/en/pricing)
- [scikit-learn histogram gradient-boosting regressor](https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.HistGradientBoostingRegressor.html)

### Painless integration boundary

- The empty frontend pages do not call a missing API, so they can ship before
  models, storage, or schedulers exist.
- Keep browser routes under `/pitcher-ks/*`, but place the future data API under
  `/api/pitcher-ks/*`. This avoids a Vite development-proxy collision between a
  directly opened frontend route and a backend endpoint using the same prefix.
- Vercel already rewrites browser paths to `index.html`, so the new tabs require
  no deployment rewrite.
- The future backend can reuse the current FastAPI router pattern and immutable
  Hit Picks run/store pattern. Only additive tables/routes are needed.
- The first models should stop at K4. Optional K5-K7 sources can be added by
  ablation later and must always have neutral/missing fallbacks.
- Cache and rate-limit public-source requests and retain source timestamps.
  Public/no-key access is not permission to make unbounded traffic.

## Evidence and external model review

These sources inform the parameter candidates, not predetermined feature
weights:

- MLB defines pitcher K% as strikeouts divided by total batters faced. That is
  the correct skill-rate denominator; K/9 mixes skill with how efficiently a
  pitcher records outs. [MLB K% glossary](https://www.mlb.com/glossary/advanced-stats/strikeout-rate)
- Baseball Savant's documented pitch data includes pitch type, velocity,
  location, batter/pitcher identity, handedness, and pitch result, with further
  movement, spin, release, and event fields available in the export.
  [Statcast CSV documentation](https://baseballsavant.mlb.com/csv-docs)
- Savant's pitcher leaderboards expose K%, BB%, Whiff%, Chase%, fastball
  velocity/spin, curve spin, and extension together. This supports treating
  underlying pitch process and observed K% as separate feature groups.
  [Statcast pitcher percentile rankings](https://baseballsavant.mlb.com/leaderboard/percentile-rankings?type=pitcher)
- Eric Martin's 2019 strikeout-rate study tested multiple statistical and ML
  models on more than 2.5 million pitches. Its random forest led the examined
  models for seasonal K%-MAE, and maximum velocity, strike rate, and vertical
  movement variation were the leading reported inputs. The study predicts
  seasonal rate, not daily total, so its result is a feature hypothesis rather
  than a daily accuracy promise.
  [Predicting MLB Strikeout Rates from Differences in Velocity and Movement](https://assets-global.website-files.com/5f1af76ed86d6771ad48324b/5f6d38971aa75c2f6af77911_Predicting-Major-League-Baseball-Strikeout-Rates-Update.pdf)
- FanGraphs describes projection systems as estimates of current true talent
  that weight full history, recency, underlying skills, age, and sample size;
  its Depth Charts explicitly separate projected performance from expected
  playing time. That same separation should exist here as K/BF versus BF.
  [FanGraphs projection systems](https://library.fangraphs.com/principles/projections/)
  and [Depth Charts](https://library.fangraphs.com/depth-charts/)
- THE BAT X's published factor list includes opponent, park, weather, umpire,
  catcher framing, bullpen, pitch counts, home field, platoon, lineup position,
  and surrounding-lineup quality. Its 2026 description adds a Statcast “stuff”
  layer using velocity, movement, and spin. This is a useful completeness
  checklist, but it is a proprietary vendor description and must not substitute
  for this project's own ablation tests.
  [THE BAT X overview](https://rotogrinders.com/the-bat)
- Catcher framing can change called strikes on taken pitches near the zone.
  [Baseball Savant catcher-framing documentation](https://baseballsavant.mlb.com/leaderboard/catcher-framing)
- MLB introduced the ABS Challenge System in 2026. Historical called-strike and
  framing effects therefore need a season/rules-regime feature, and any
  challenge data available pregame or historically should be treated explicitly.
  [Baseball Savant ABS documentation](https://baseballsavant.mlb.com/abs)
- Research on times through the order finds gradual within-game deterioration
  and selection bias in which pitchers survive to face more batters. This
  argues for modeling removal/workload continuously rather than applying a
  universal third-time-through cutoff.
  [Bayesian analysis of the time-through-the-order penalty](https://arxiv.org/abs/2210.06724)
- The locked scikit-learn version already provides `PoissonRegressor` and
  `HistGradientBoostingRegressor` with Poisson and quantile losses, including
  native missing-value support. An initial count-model challenger therefore
  needs no new ML dependency.
  [PoissonRegressor](https://scikit-learn.org/stable/modules/generated/sklearn.linear_model.PoissonRegressor.html)
  and [HistGradientBoostingRegressor](https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.HistGradientBoostingRegressor.html)

## The top three approaches

The ranking below is by expected long-term product quality. The recommended
development order is the reverse: build Approach 3 first, then 2, then 1.

| Rank | Approach | Accuracy ceiling | Distribution quality | Explainability | Build cost | Recommended role |
|---:|---|---|---|---|---|---|
| 1 | Decomposed PA matchup + workload/removal + Monte Carlo | Highest | Best if calibrated | High at component level | High | Production candidate |
| 2 | Direct gradient-boosted count/quantile ensemble | High | Moderate to high | Moderate | Medium | Strong challenger |
| 3 | Shrunk empirical-Bayes/log5 rate × workload | Moderate | Good, simple | Highest | Low | Benchmark and fallback |

### Approach 1: decomposed plate-appearance and workload simulation

#### Model structure

1. Freeze the day's expected starter and projected/confirmed opposing lineup.
2. Estimate a strikeout probability for each expected pitcher-batter plate
   appearance.
3. Estimate how long the pitcher remains in the game, using a batters-faced or
   removal-hazard model.
4. Simulate the lineup cycling through plate appearances until the pitcher is
   removed or the game state ends his appearance.
5. Aggregate simulated strikeouts into a coherent probability mass function.

An MVP can use two components:

```text
p_K[pitcher, batter, matchup]
P(total batters faced = n | pitcher, opponent, workload context)
```

The fuller version uses a survival/removal probability after each batter faced:

```text
P(removed after BF=t | pitches, outs, baserunners, runs, rest,
                       manager tendency, bullpen state, lineup cycle)
```

The pregame simulator must not pretend to know actual in-game runs or pitch
count. It samples them from pregame-estimated component distributions. A
simpler and safer first simulation samples BF directly, then traverses the
lineup and samples Ks from the per-PA probabilities.

#### Suggested estimators

- Per-PA K model: regularized logistic regression baseline followed by
  `HistGradientBoostingClassifier`.
- BF model: empirical distribution/quantile baseline followed by either a
  multiclass BF bucket classifier or three quantile
  `HistGradientBoostingRegressor` models.
- Removal hazard: discrete-time logistic or gradient-boosted survival model.
- Simulation: NumPy/SciPy, 5,000-20,000 draws per starter after convergence
  testing.

#### Strengths

- Separates pitcher strikeout skill from opportunity.
- Naturally uses the actual expected lineup and batting-order sequence.
- Returns a full, internally consistent distribution for any threshold.
- Explains misses: K-rate miss, BF/workload miss, starter scratch, or lineup
  miss.
- Can later extend to innings, hits allowed, walks, and fantasy points without
  rebuilding the game simulator.

#### Weaknesses

- More components can each be miscalibrated.
- Historical projected-lineup fidelity is difficult.
- A realistic removal process has selection bias: poor performance causes an
  early hook, and efficient/high-K starts can both spend more pitches and
  record outs faster.
- Simulation can appear sophisticated while merely compounding weak inputs;
  every component needs its own evaluation.

#### Promotion requirement

Approach 1 should beat Approach 2 on full-distribution score or materially
improve tail calibration. Similar MAE alone is not enough to justify its added
complexity.

### Approach 2: direct count-regression and quantile ensemble

#### Model structure

Create one pregame row per starter-game and predict actual strikeouts directly.

Candidate ladder:

1. `PoissonRegressor` with standardized numeric features.
2. `HistGradientBoostingRegressor(loss="poisson")` for the expected count.
3. Quantile HGB regressors for the 10th, 25th, 50th, 75th, and 90th
   percentiles.
4. Fit Poisson and negative-binomial residual distributions around the OOS
   mean; select by held-out log score and interval coverage.
5. Optionally blend the direct mean with Approach 3 using weights selected only
   on development folds.

If observed variance substantially exceeds the mean, the Poisson distribution
is too narrow. Use a negative-binomial or empirical residual distribution for
the PMF; SciPy already supplies the negative-binomial functions.

#### Strengths

- Fastest route to a strong nonlinear ML model.
- Uses the existing locked stack and missing-value handling.
- Easy to ablate feature groups.
- One daily row per starter makes training and inference inexpensive.

#### Weaknesses

- Confounds K skill and workload inside one target.
- A point regressor does not automatically yield trustworthy probabilities.
- Direct models can learn unstable shortcuts such as team, season, or lineup
  quality proxies.
- Quantiles trained separately can cross and require a monotonic repair.

#### Promotion requirement

It must improve paired walk-forward MAE and distribution log score over
Approach 3, show no meaningful systematic bias, and remain stable across
projected/confirmed lineup states and pitcher workload tiers.

### Approach 3: empirical-Bayes/log5 matchup baseline

#### Model structure

Estimate shrunk pitcher and batter K rates with older history receiving less
weight. Combine pitcher, batter, and league K talent on the log-odds scale:

```text
logit(p_matchup_K) = logit(p_pitcher_K)
                   + logit(p_batter_K)
                   - logit(p_league_K)
                   + measured platoon/context adjustments
```

For each projected lineup slot, calculate the shrunk matchup rate. Cycle
through the lineup for the projected BF distribution. With fixed BF and
different batter probabilities, total Ks follow a Poisson-binomial
distribution; uncertainty in BF can be mixed over that distribution.

Initial workload can use a shrunk combination of:

- season and prior-season BF/start;
- last 3/5 starts BF and pitch count;
- days rest and known pitch limit;
- short-start frequency;
- opponent PA/run environment; and
- team/manager baseline hook tendency.

#### Strengths

- Fully interpretable and easy to reproduce.
- Robust for smaller samples because shrinkage is explicit.
- Produces a distribution without training a black-box count model.
- Establishes whether ML adds value beyond the basic baseball arithmetic.
- Reliable fallback when rich pitch/context sources are missing.

#### Weaknesses

- Additive log-odds assumptions miss nonlinear arsenal interactions.
- Workload is still difficult and may dominate total error.
- Hand-authored context adjustments can become unmeasured folklore; every
  adjustment must be learned or justified from historical data.

#### Permanent role

Keep this model even if it loses. If a more complex model cannot consistently
beat it out of sample, the complex model should not ship.

## Parameter inventory

Availability labels:

- **Existing:** already present in the database, MLB cache, or V3 feature code.
- **Derived:** can be built from existing feeds/cache without a new provider.
- **New:** requires new ingestion or persisted snapshots.
- **Optional:** valuable only after coverage, licensing, and point-in-time
  history are proven.

### 1. Slate, identity, and prediction-time contract

| Parameters | Why they matter | Availability | Priority |
|---|---|---|---|
| `game_date`, `game_pk`, doubleheader game number | Correct game identity and grading | Existing | Required |
| scheduled start time and timezone | As-of validity, lineup/weather availability | Existing | Required |
| pitcher MLB ID, name, team | Aggregation and display; ID not an unrestricted tree feature | Existing | Required |
| probable-starter status and source | Scratches invalidate every downstream feature | Existing/Derived | Required |
| projected starter probability | Quantifies starter uncertainty | Existing pattern; extend to pitchers | Required |
| role: traditional starter, opener, bulk, spot start | Workload distributions differ sharply | Derived/New | Required |
| pitcher hand; opponent batter side | Platoon and pitch-mix interaction | Existing | Required |
| home/away, venue, league, DH/rules regime | Opportunity and era effects | Existing/Derived | Required |
| `as_of_timestamp`, morning/afternoon window | Prevents future information leakage | Existing pattern | Required |
| lineup source/provider/confirmation/confidence | Measures input uncertainty | Existing | Required |

### 2. Pitcher true-talent strikeout skill

| Parameters | Why they matter | Availability | Priority |
|---|---|---|---|
| K/BF for current season, prior seasons, and career | Core strikeout skill; use BF denominator | Existing/Derived | Required |
| rolling K/BF over last 3, 5, 10 starts and 30/60 days | Detects recent skill change; must be shrunk | Derived | Required |
| exponentially weighted K/BF | Smoother alternative to arbitrary windows | Derived | High |
| K/9 | Familiar display/benchmark; not preferred core rate | Existing | Benchmark |
| swinging-strike/whiff rate | Underlying bat-missing skill | Existing pitch cache/V3 | Required |
| called-strike rate and CSW-like rate | Captures taken strikes plus whiffs | Derived | High |
| overall strike %, zone %, chase %, in-zone contact, out-of-zone contact | Describes count leverage and finishing ability | Derived | High |
| first-pitch-strike rate | Getting ahead changes K opportunity | Derived | High |
| two-strike put-away rate and 0-2 conversion | Ability to finish plate appearances | Derived | High |
| foul rate with two strikes; pitches per PA | Extends or ends K opportunities and affects workload | Derived | Medium |
| BB%, HBP%, WHIP, FIP, xwOBA allowed | Inefficiency and early-removal risk, not direct K bonuses | Existing/Derived | High |
| platoon K% versus L/R batters | Handedness-specific skill | Derived | Required |
| first/second/third lineup-cycle K% | Within-game skill evolution; shrink strongly | Derived | Medium |
| age and MLB experience | True-talent/cold-start priors and change risk | Existing API/New snapshot | Medium |
| minor-league/foreign K% translated to MLB | Rookie and returnee priors | New/Optional | Later |

### 3. Pitch quality and arsenal

| Parameters | Why they matter | Availability | Priority |
|---|---|---|---|
| pitch-type usage and usage entropy | Defines the arsenal actually used | Existing V3 | Required rich-model group |
| average/max velocity by pitch family | Stuff and current health/intent | Existing/Derived | Required rich-model group |
| velocity change from prior season and last 3 starts | Detects repertoire/health changes faster than K results | Derived | High |
| horizontal and induced vertical movement by family | Bat-missing shapes and pitch separation | Existing V3 | Required rich-model group |
| movement range/IQR and pairwise separation across pitch families | Published strikeout-rate work supports arsenal differences | Derived | High |
| release extension and release-point consistency | Perceived velocity/deception and health change | Existing V3/Derived | High |
| spin rate, active-spin/spin-direction proxies | Shape and change detection | New fields from existing pitch source | Medium |
| pitch-family whiff, chase, called-strike, K%, and put-away rates | Identifies actual out pitches | Partly existing; extend | Required rich-model group |
| batter performance versus each pitch family | Lineup-specific arsenal fit | Existing V3 base; extend to K outcome | Required rich-model group |
| expected arsenal-vs-lineup whiff/K rate weighted by pitcher usage | Condenses matchup without raw BvP noise | Derived from V3 pattern | Required rich-model group |
| pitch data sample sizes and missing flags | Prevents small samples from becoming extreme skill | Existing pattern | Required |

### 4. Opposing lineup and batter matchup

| Parameters | Why they matter | Availability | Priority |
|---|---|---|---|
| projected/confirmed nine hitters and batting order | Determines who the pitcher is likely to face and how often | Existing | Required |
| each batter's season/prior/career K% | Core opponent K tendency | Existing/Derived | Required |
| batter K% versus pitcher hand | Platoon-specific opponent tendency | Derived | Required |
| rolling batter K%, whiff%, chase%, contact% | Detects meaningful approach changes; shrink by sample | Partly existing V3 | High |
| batter pitch-family whiff/chase/contact | Arsenal-match component | Existing V3; extend | High |
| lineup-weighted K% for first 9, top 6, and likely first two cycles | Compact lineup summaries for direct models | Derived | Required |
| lineup projected PA/BF shares by slot | Converts batter matchup into total opportunity | Derived | Required |
| lineup confidence and number of unresolved slots | Prediction uncertainty and coverage | Existing | Required |
| expected pinch-hit/substitution probability | Changes late lineup matchups | New/Optional | Later |
| opponent BB%, contact quality, wOBA, runs/PA | Affects pitch efficiency and early-hook risk | Existing/Derived | High |
| batter-vs-pitcher history | Highly sparse; use only with strong shrinkage and an ablation | Existing approximation | Experimental |
| opponent roster injuries/rest/travel | Can change lineup quality before confirmation | New/Optional | Later |

### 5. Workload, removal, and pitcher availability

| Parameters | Why they matter | Availability | Priority |
|---|---|---|---|
| BF, outs/IP, and pitches in last 3/5/10 starts | Most direct workload history | Existing/Derived | Required |
| season/prior-season BF/start, IP/start, pitches/start | Stable workload baseline | Existing/Derived | Required |
| days rest and starts in recent calendar windows | Fatigue and schedule context | Existing V3 | Required |
| most recent pitch count and recent maximum | Likely current leash | Existing | Required |
| known pitch count/innings limit | Rehab, debut, return from IL, or planned tandem | New/Optional but high value | Required when available |
| short-start/opening frequency | Identifies role and early-hook baseline | Existing V3 | Required |
| team/manager removal tendency after BF/pitches/TTO | Workload policy differs by club and pitcher | Derived | High |
| starter's historical removal hazard conditional on workload | Individual leash | Derived | High |
| opposing lineup PA, BB, wOBA, and run environment | Controls efficiency and blow-up risk | Derived | High |
| starter's BB/HBP/HR/baserunner rates | Controls pitch cost and early removal | Existing/Derived | High |
| own bullpen availability, quality, and recent workload | Managers can hook earlier with a fresh strong bullpen | Existing V3 workload base; extend | Medium |
| team game importance/playoff context | May change leash | New/Derived | Later |
| doubleheader and recent team schedule | Can change bullpen and starter usage | Existing/Derived | Medium |
| injury/IL transaction, velocity warning, skipped start | Availability and hidden limits | New/Optional | High-value later |

### 6. Catcher, umpire, rules, park, and weather

| Parameters | Why they matter | Availability | Priority |
|---|---|---|---|
| expected catcher and framing/shadow-strike rate | Taken pitches near the boundary can become strikes | New/Optional | Medium |
| expected home-plate umpire and called-strike tendency | Affects called strikes; often unavailable early | New/Optional | Medium |
| 2026 ABS Challenge regime and historical rules-era flag | Called-strike process changed across seasons | Derived/New | Required era control |
| team/player challenge tendency and successful overturn rate | May affect called strike threes in 2026+ | New/Optional | Research |
| Statcast park K factor or venue K environment | Some venues/sightlines affect K rate | Existing source; add K-specific field | Medium |
| run/HR park factors | Indirect early-removal and pitch-efficiency context | Existing | Medium |
| altitude | Pitch movement/run environment | Derived | Medium |
| temperature, humidity, pressure/air density, wind | Potential movement/run-environment effects | New | Experimental |
| roof state and precipitation/delay risk | Delay can end a starter's outing regardless of performance | New | High when severe |
| day/night and local start time | Visibility and routine effects; require evidence | Existing/Derived | Experimental |

Weather, umpire, catcher, and park groups should enter only after coverage and
point-in-time history are audited. They must beat an explicit neutral fallback
in chronological ablations.

### 7. Market and external projection parameters

| Parameters | Use | Availability | Rule |
|---|---|---|---|
| pitcher K line and over/under odds at prediction time | External benchmark and later edge calculation | Existing empty storage schema; no ingestion | Keep out of core model initially |
| moneyline and game total | Summaries of win/run environment | New | Test only in a separately labeled market-assisted model |
| public season K% projection | True-talent benchmark/prior if usage terms allow | New/Optional | Snapshot with source and timestamp |
| public projected IP/BF | Workload benchmark | New/Optional | Never mix future/closing updates into morning tests |

The independent model and market-assisted model must have different version
names, reports, and UI labels. Otherwise market information can make a baseball
model appear stronger while eliminating the independent comparison the product
needs.

### 8. Data-quality and provenance parameters

Every score should carry:

- feature snapshot timestamp;
- starter-source timestamp and confidence;
- lineup source, confirmation state, confidence, and missing slots;
- pitcher, batter, pitch, and workload sample sizes;
- missing/fallback flags by feature group;
- season/rules regime;
- source freshness for weather/catcher/umpire/market data;
- model recipe, feature schema, dependency lock, dataset, calibrator, and code
  fingerprints; and
- a complete candidate cohort hash.

These are not decorative metadata. They are needed to slice performance by
coverage and to reproduce any displayed projection.

## Feature-development ladder

Do not put all parameters into the first model. Add feature groups in a locked
ladder and retain every out-of-sample result.

| Experiment | Feature set | Question |
|---|---|---|
| K0 | League/pitcher/batter shrunk K% + simple BF | Can basic baseball arithmetic be reproduced? |
| K1 | K0 + richer workload/rest/removal features | How much error is opportunity rather than K skill? |
| K2 | K1 + projected lineup and handedness | Does the actual expected opponent improve daily totals? |
| K3 | K2 + pitch-process metrics (whiff, chase, strikes, put-away) | Do underlying skills beat observed K% alone? |
| K4 | K3 + pitch shape, velocity change, and arsenal-lineup matching | Does “stuff” and repertoire fit add stable OOS value? |
| K5 | K4 + catcher/umpire/ABS context | Are called-strike effects measurable with honest coverage? |
| K6 | K5 + park/weather/roof | Does environment improve forecast or only add noise? |
| K7 | Best independent model + time-stamped markets | Does market information add value? Label separately. |

Each experiment changes one interpretable group. A group that fails the
development gates stays out even if an individual feature looks plausible.

## Historical dataset design

### Row identities and labels

Build two related point-in-time datasets.

#### Starter-game table

One row per `(game_pk, pitcher_id, prediction_window)` with:

- `actual_strikeouts` as the primary label;
- `actual_batters_faced`, `actual_outs`, `actual_innings`, and
  `actual_pitches` as component labels;
- `actual_started`, opening/bulk role, and scratch/replacement outcome;
- optional removal BF/pitch count and removal-reason proxies; and
- only features available at the frozen `as_of_timestamp`.

#### Pitcher-batter PA table

One row per actual or historically projected pitcher-batter plate appearance:

- binary `is_strikeout` label;
- batter sequence number and lineup cycle;
- pitcher, batter, handedness, arsenal, and pregame context;
- no pitch result or same-game state in a pregame model; and
- optional separate in-game table only if a future live model is developed.

For the pregame simulation, historical expected plate appearances must come
from the point-in-time projected lineup, not the final batting order. Actual PA
rows can train the conditional K model only if selection into those PAs is
handled honestly and evaluation still scores the full projected cohort.

### Replay order

For every historical date:

1. Load schedule and game metadata known by the prediction window.
2. Build projected lineups from earlier games only.
3. Snapshot all pitcher/batter/pitch/workload histories before that day's
   games are added.
4. Create starter-game and matchup features.
5. After every row for the date is frozen, add final games to history.
6. Store outcomes separately from feature snapshots.

This mirrors the safe pattern already used by `HitDatasetBuilder`.

### Leakage rules

Never use:

- final starter identity when the probable starter differed at prediction time;
- official batting order in a historical morning run;
- same-day pitch or outcome data;
- end-of-season aggregates for an earlier date;
- current rolling park factors applied to earlier seasons;
- a known postgame injury, pitch limit, or transaction;
- odds captured after the modeled prediction time;
- a closing K line in a model advertised as a morning projection; or
- actual BF/innings as a feature in the K total model.

Persist explicit neutral/missing values instead of silently dropping hard
starters. Dropping rows with missing rich features can make the candidate look
better by changing the evaluation cohort.

### Training history

- Start with 2023 onward because the repository already has historical parquet
  and game-cache infrastructure around 2023-2026.
- Audit pitch-field coverage by season before fixing the lower bound. If a rich
  pitch field is structurally missing in early seasons, either remove it,
  create an era-aware fallback, or train a rich challenger on a later common
  period while keeping every comparison paired.
- Exclude spring training initially. Report postseason separately because
  pitcher usage policies differ.
- Treat seven-inning games, suspended games, doubleheaders, openers, and bulk
  roles explicitly rather than folding them into normal starts.

## Backtesting and model selection

### Baselines that every model must beat

1. Pitcher's last-start K total.
2. Pitcher's rolling last-3-start average K total.
3. Season K/BF multiplied by season-average BF/start.
4. Shrunk pitcher K/BF × projected BF.
5. Full Approach 3 opponent-adjusted empirical-Bayes model.
6. If legally and historically available, the time-matched public projection
   and no-vig sportsbook line as external comparisons—not training truth.

### Chronological folds

Freeze exact dates after the coverage audit, using a structure such as:

1. Train through 2023; test multiple early/late 2024 blocks.
2. Train through each 2024 cutoff; test multiple 2025 blocks.
3. Train through 2025; use early/mid-2026 blocks for development.
4. Reserve the most recent complete 2026 block as a locked final test.
5. Begin a live shadow ledger only after the final candidate and thresholds are
   frozen.

No random K-fold cross-validation. Starts by the same pitcher and games on the
same date are dependent and baseball changes across seasons.

Evaluate morning and afternoon modes separately, plus a paired subset where
both modes/models have the exact same starter cohort.

### Primary metrics

#### Point forecast

- Mean absolute error (MAE): primary point metric.
- Median absolute error.
- Root mean squared error: penalizes large misses.
- Mean signed error overall and by predicted-K band.
- Poisson deviance for count-model comparison.
- Percentage within 1 K and within 2 Ks, reported as descriptive metrics only.

#### Full distribution

- Negative log likelihood/log score of the observed K count.
- Ranked probability score or discrete CRPS across the K CDF.
- Coverage and width of 50%, 80%, and 90% prediction intervals.
- Probability calibration for `K >= 3`, `K >= 4`, through practical upper
  thresholds, with sample counts.
- Brier score and reliability diagram for common half-run prop lines.
- Sharpness: a trivially wide interval must not win merely by covering more.

For a line `n + 0.5`:

```text
P(over n.5) = P(K >= n + 1)
```

All displayed threshold probabilities must come from one coherent PMF. Do not
fit unrelated binary models whose `P(5+)` can be lower than `P(6+)`.

### Component metrics for Approach 1

- Per-PA K log loss, Brier score, ROC AUC, and calibration.
- BF MAE and quantile coverage.
- Outs/IP and pitch-count MAE as workload diagnostics.
- Removal-survival calibration by batter-sequence band.
- Simulated versus observed BF, K, and K/BF distributions.
- Attribution of total K error to K-rate versus workload error.

### Required slices

- Season and month.
- Morning projected versus afternoon confirmed lineup.
- Starter, opener, bulk, pitch-limited, and unknown role.
- Left- versus right-handed pitcher.
- Rookie/low-sample versus established pitcher.
- Projected BF and pitch-count tier.
- Rest-days tier.
- Opponent lineup K% tier.
- Arsenal-match coverage tier.
- Park/roof/weather availability.
- Catcher/umpire availability.
- ABS/rules regime.
- Day/night, home/away, and doubleheader.
- Model data-quality/confidence tier.

### Uncertainty and statistical comparison

- Compare candidates on the same starter-game rows.
- Bootstrap complete game dates so the whole daily slate stays together.
- Report 95% confidence intervals for metric differences.
- Add a pitcher-cluster sensitivity analysis so repeated starts by one pitcher
  do not dominate a conclusion.
- Calculate the minimum detectable improvement before setting final promotion
  thresholds.
- Require directionally consistent results across most chronological folds;
  one hot month is not sufficient.

### Provisional promotion gates

Freeze numeric gates after K0 establishes realistic variance and MDE. A
reasonable starting contract is:

- Candidate improves paired MAE by at least 3% versus Approach 3, with a 95%
  interval that rules out a material decline.
- Candidate improves distribution log score/CRPS by at least 2% or delivers a
  clearly material interval-calibration improvement.
- Mean signed error stays within 0.15 K overall and within 0.30 K for every
  adequately sized projected-K band.
- 80% interval coverage is within 76%-84% without materially excessive width.
- Threshold probability calibration has no systematic over/under bias greater
  than 5 percentage points in adequately sized buckets.
- At least four of five development folds are nonnegative on primary metrics.
- Starter coverage is at least 95%, with missing rows explained rather than
  silently removed.
- Projected-lineup mode passes independently; confirmed-lineup performance
  cannot hide a poor morning model.
- Daily inference completes inside the scheduled window and every displayed
  run is reproducible from its manifest.

The locked final block is opened once. Failure requires a new hypothesis and a
new final period, not tuning against the failed holdout.

## Calibration and probability construction

### Approach 1

- Calibrate per-PA K probabilities on later out-of-sample predictions.
- Validate and, if needed, calibrate the BF/removal distribution separately.
- Combine calibrated components in simulation.
- Inspect the final K CDF. If post-hoc calibration is needed, use a method that
  preserves monotonicity across K thresholds.

### Approach 2

- Fit the mean on training folds.
- Estimate Poisson, negative-binomial, and empirical residual dispersion using
  only OOS calibration predictions.
- Fit quantile models on training data and repair quantile crossing without
  looking at the final test.
- Select the distribution method by held-out log score, CRPS, coverage, and
  sharpness—not MAE alone.

### Approach 3

- Tune empirical-Bayes prior strengths only on development periods.
- Mix over the BF distribution rather than treating projected BF as certain.
- Check tail calibration; a simple beta-binomial/negative-binomial mixture may
  be needed if independent PAs understate game-level variation.

Calibration data must be later than base-model training and earlier than the
calibration test. A calibrator is bound to the model recipe, feature schema,
data window, and dependency environment.

## Explainability

Every projection should show a small set of baseball-readable drivers, for
example:

- pitcher true-talent K/BF versus league;
- opposing projected lineup K tendency;
- arsenal-match whiff tendency;
- projected BF/pitch-count leash;
- rest or known pitch limit;
- lineup confidence; and
- important missing/fallback context.

Use component deltas or permutation-based explanation derived from the fitted
model. Do not create prose that implies a causal effect from a correlated tree
feature. Global reports should include permutation importance, partial
dependence for core continuous features, missingness dependence, and ablation
results.

## Proposed product/API contract

### Daily output per pitcher

```json
{
  "game_pk": 123456,
  "game_date": "2026-08-01",
  "pitcher_id": 999999,
  "pitcher_name": "Example Starter",
  "team": "Example Team",
  "opponent": "Opponent",
  "role": "starter",
  "projected_ks": 6.2,
  "median_ks": 6,
  "mode_ks": 6,
  "p10_ks": 3,
  "p90_ks": 9,
  "probabilities": {
    "3_plus": 0.91,
    "4_plus": 0.84,
    "5_plus": 0.72,
    "6_plus": 0.55,
    "7_plus": 0.37,
    "8_plus": 0.22
  },
  "projected_batters_faced": 23.8,
  "projected_pitches": 91.0,
  "lineup_source": "projected",
  "lineup_confidence": 0.78,
  "projection_confidence": "medium",
  "model_version": "pitcher_ks_decomposed_v1",
  "as_of_timestamp": "2026-08-01T15:00:00Z"
}
```

### Candidate routes

- `GET /api/pitcher-ks/today`
- `GET /api/pitcher-ks/{date}`
- `GET /api/pitcher-ks/pitchers/{pitcher_id}/history`
- `GET /api/pitcher-ks/models/metrics`
- optional later: `POST /api/pitcher-ks/prop-edge`

The initial page should rank the full starting-pitcher slate, allow date and
model selection, distinguish projected from confirmed lineups, show intervals
instead of false precision, and expose historical actual Ks alongside the
frozen projection.

## Persistence and run identity

Follow the immutable Hit Picks pattern with separate run and prediction tables.

### Proposed `pitcher_k_runs`

- `run_id`
- `projection_date`
- `generated_at`
- `as_of_timestamp`
- `prediction_window`
- `model_version` and `model_role`
- `is_visible`, `is_evaluation`, and publication pointer semantics
- `comparison_group_id` and cohort hash
- model/feature/data/calibration/dependency/code fingerprints
- source freshness and coverage summaries
- run status, failure reason, runtime, and row count

### Proposed `pitcher_k_predictions`

- `run_id`, `game_pk`, `pitcher_id`
- pitcher/team/opponent/role display fields
- mean/median/mode and interval values
- PMF JSON or normalized threshold/CDF representation
- projected BF, outs/IP, and pitches
- lineup/starter/confidence/provenance fields
- top-driver JSON
- actual Ks/BF/IP/pitches and grading metadata
- skip/pending reason for postponement, scratch, opener mismatch, or no appearance

Uniqueness should be `(run_id, game_pk, pitcher_id)`. Never overwrite a morning
projection with an afternoon projection or a rerun.

## Proposed code/artifact layout

V1 implements the compact `pitcher_ks/features.py`, `modeling.py`, and
`store.py` core plus the train/predict scripts. The larger layout below is the
planned expansion path for calibration, grading, and later feature tiers:

```text
backend/
  pitcher_ks/
    __init__.py
    dataset.py
    features.py
    baselines.py
    count_model.py
    pa_model.py
    workload.py
    simulation.py
    calibration.py
    evaluation.py
    experiment_contract.py
    store.py
  config/
    pitcher_ks_experiment.json
    pitcher_ks_candidate.json
  routers/
    pitcher_ks.py
  scripts/
    build_pitcher_k_dataset.py
    evaluate_pitcher_k_ladder.py
    predict_pitcher_ks_today.py
    grade_pitcher_ks.py
  tests/
    test_pitcher_k_dataset.py
    test_pitcher_k_features.py
    test_pitcher_k_baselines.py
    test_pitcher_k_models.py
    test_pitcher_k_simulation.py
    test_pitcher_k_calibration.py
    test_pitcher_k_store.py
    test_pitcher_k_routes.py
  backtest_results/
    pitcher_ks/                 # large, gitignored
  reports/
    pitcher_ks/                 # small reviewed summaries
```

## Implementation phases

### Phase 0: freeze definitions and audit data

- Define a strikeout total, credited pitcher, starter/opener/bulk policy,
  suspended-game policy, and doubleheader identity.
- Inventory 2023-2026 game-feed coverage for outcomes, BF, pitches, pitch
  descriptions, velocity, movement, spin, extension, lineup, catcher, and ABS
  fields.
- Quantify probable-starter and point-in-time projected-lineup accuracy.
- Freeze development/final windows only after coverage is known.
- Create a versioned experiment contract and baseline artifact manifest.

**Exit:** the dataset can be built without future data, all exclusions are
enumerated, and final dates are locked.

### Phase 1: build the point-in-time dataset

- Reuse the shared MLB cache and chronological history objects.
- Add starter-game labels and pregame features.
- Add PA-level K labels and matchup features.
- Build league/season priors and sample-size/missing indicators.
- Add unit tests that intentionally attempt final-lineup, final-starter,
  same-day, and season-end leakage.
- Produce a coverage and target-distribution report.

**Exit:** repeat builds produce identical row counts, hashes, and features.

### Phase 2: implement Approach 3 benchmark

- Fit/tune shrunk pitcher, batter, platoon, and league K rates.
- Build simple then richer projected-BF distributions.
- Generate a full K PMF and threshold probabilities.
- Run chronological backtests and freeze the benchmark package.

**Exit:** the benchmark is reproducible, calibrated enough to serve as a
fallback, and defines the MDE for challengers.

### Phase 3: implement Approach 2 count challenger

- Run `PoissonRegressor`, Poisson HGB, negative-binomial dispersion, and
  quantile models.
- Add feature groups K1-K6 one at a time.
- Tune only within chronological development folds.
- Compare component-free direct predictions against Approach 3.

**Exit:** freeze the best direct-count recipe or reject the approach.

### Phase 4: implement Approach 1 decomposed challenger

- Train and calibrate the per-PA K model.
- Train BF distribution or removal-hazard components.
- Add lineup traversal and Monte Carlo simulation.
- Test simulation convergence, PMF sums, threshold monotonicity, and seeded
  determinism.
- Attribute errors to matchup and workload components.

**Exit:** the decomposed model beats the direct model on distribution quality
  enough to justify its complexity, or the direct model remains champion.

### Phase 5: locked evaluation and shadow mode

- Freeze one candidate, feature schema, calibration procedure, and environment.
- Open the final block once.
- If gates pass, write daily shadow projections without public promotion.
- Require at least 30 completed game dates and roughly 400 graded starters,
  then recalculate confidence intervals and drift slices.
- Compare morning, afternoon, and any time-matched market benchmarks.

**Exit:** promote, continue shadow, or reject with a written decision.

### Phase 6: production integration

- Add immutable storage, routes, daily prediction, and next-day grading.
- Schedule morning and afternoon runs with failure isolation from Hit Picks.
- Publish only after the entire slate is scored and persisted successfully.
- Add source-freshness checks, bounded runtime, healthcheck pings, log rotation,
  and a last-known-good fallback.
- Add the reader page and historical audit only after the API/run contract is
  stable.

**Exit:** production runs are atomic, observable, reproducible, and safe when
optional sources fail.

## Operational behavior

- Morning run uses projected lineups and carries wider uncertainty.
- Afternoon run uses confirmed lineups where available and remains a separate
  immutable evaluation window.
- A starter scratch creates a new run; it does not mutate the earlier
  projection.
- A missing optional weather/umpire/catcher source falls back to neutral plus a
  visible missing flag.
- A missing starter, corrupt core dataset, model/schema mismatch, or invalid PMF
  fails closed and does not publish a partial board.
- Approach 3 can be the last-known-good fallback only when the fallback is
  clearly identified in the run metadata.
- Grading waits for final games and records postponements, suspended games,
  opener mismatches, and did-not-pitch cases explicitly.

## Testing requirements

### Unit tests

- Exact innings/outs and BF calculations.
- K/BF and shrinkage math.
- Log5 matchup edge cases and league-average identity.
- BF mixture and Poisson-binomial PMF sums to 1.
- Negative-binomial parameter conversion.
- Threshold probabilities are monotone.
- Quantile crossing repair.
- Seeded simulation determinism and convergence tolerance.
- Doubleheader and opener/bulk identities.
- Missing starter/lineup/pitch/weather/catcher fallbacks.

### Point-in-time tests

- Same-day games do not enter rolling history.
- Projected historical lineups use only prior games.
- Morning rows never use final lineups.
- Current park/weather/market snapshots are not backfilled into old rows.
- Calibration-fit and calibration-test dates do not overlap.
- Final test cannot run until a candidate fingerprint is frozen.

### Integration tests

- Historical dataset and daily scorer create the same features for a frozen
  fixture.
- All approaches score an identical cohort hash.
- Store/API round-trip preserves the PMF and run provenance.
- Re-running creates a new immutable run without double-counting evaluation.
- Partial database failure leaves the prior public run intact.
- Grader handles final, postponed, suspended, scratched, and opener games.

## Risks and mitigations

| Risk | Consequence | Mitigation |
|---|---|---|
| Workload dominates K skill | Good K-rate model still misses totals | Separate BF/removal component and grade component errors |
| Historical lineup leakage | Unrealistically strong backtest | Recreate projected lineups using prior games only; separate morning/afternoon |
| Starter scratches/openers | Projection belongs to wrong pitcher/role | Starter confidence, immutable reruns, role classification, explicit grading |
| Small pitcher/batter/pitch-type samples | Extreme noisy rates | Empirical-Bayes shrinkage, sample fields, neutral fallbacks |
| Survivor bias in long starts | Removal model learns from selected pitchers | Discrete hazard framing, workload covariates, survival diagnostics |
| Poisson under-dispersion | Tail probabilities too confident | Negative-binomial/empirical mixture and interval validation |
| Separate quantile crossing | Impossible intervals | Monotonic post-processing and PMF validation |
| 2026 ABS regime shift | Historical called-strike relationships drift | Era flag, challenge features, recent fold monitoring |
| Weather/umpire/catcher gaps | Biased cohort or brittle daily run | Optional tiers, point-in-time coverage audit, explicit neutral fallback |
| New pitch or velocity change | Season aggregates adapt too slowly | Rolling pitch-shape/velocity deltas and drift alerts |
| Proprietary source dependence | Cost, licensing, or reproducibility failure | Core model uses owned/public data; vendor outputs only optional benchmarks |
| Market leakage | Inflated model evaluation | Independent and market-assisted versions, time-stamped odds, paired reports |
| Multiple experimentation | False discovery | Frozen ladder, locked final block, confidence intervals, written rejection logs |

## Recommended first implementation choice

Build Approach 3 as K0/K1, then Approach 2 through K4. Only after those
benchmarks are frozen should the project pay the complexity cost of Approach 1.

The expected production winner is Approach 1, but the decision must be earned:

```text
If decomposed simulation improves distribution quality materially:
    promote it and keep direct count + empirical Bayes as challengers/fallbacks
else if direct count improves paired MAE and calibration:
    promote direct count and keep empirical Bayes as fallback
else:
    ship only the transparent empirical-Bayes projection or continue research
```

This ordering provides a useful projection early, establishes honest baselines,
and prevents a complex simulator from being declared successful without proof
that its extra machinery improves daily forecasts.

## Definition of done

`Pitcher_Ks` is ready for public use only when:

- every displayed pitcher is tied to `game_pk`, pitcher ID, role, prediction
  window, and as-of timestamp;
- historical and daily features share one point-in-time code path;
- the selected model beats the frozen empirical-Bayes benchmark on paired
  chronological tests;
- MAE, bias, full-distribution score, threshold calibration, and interval
  coverage pass the frozen gates;
- projected-lineup performance passes independently of confirmed-lineup mode;
- the model survives the locked final block and live shadow period;
- source freshness, missingness, and starter/lineup uncertainty are visible;
- a saved run can be reproduced from its data/model/environment fingerprints;
- daily publishing is atomic and failure-isolated; and
- actual outcomes are graded into an audit history without overwriting the
  original projection.
