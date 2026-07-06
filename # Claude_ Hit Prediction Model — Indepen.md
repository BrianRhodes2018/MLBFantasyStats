# Claude_ Hit Prediction Model — Independent Formula + Betting Page Wiring

## Context

You want an **independent answer** to "will this batter get ≥1 hit today?" — built from this season's (2026) data, kept fully separate from the existing hand-tuned hit model via a `Claude_` prefix on every new file, and wired into the Betting page as its own section. **No files will be deleted.**

The existing model is a hand-weighted signal blend (form 0.44 / pitcher 0.34 / platoon 0.19 / park 0.03) tuned by grid search on hit rate. My independent approach: **fit a logistic regression on every batter-game of the 2026 season** with leakage-free pregame features, so the data itself decides the weights, and the output is a calibrated probability with an interpretable formula.

## Answer to "what else should I take into account?"

Beyond your five factors (recent form, pitcher quality, lineup slot, platoon, park), the model will add — and the season report will quantify — these:

1. **Batter strikeout rate (rolling + season)** — the single best free predictor of hitless games; not on your list.
2. **Season-long hit rate per PA with small-sample shrinkage** — season talent typically beats recent form for predicting hits; shrinking early-season rates toward league average prevents April noise from dominating.
3. **Expected plate appearances** — lineup slot matters mostly because slot 1 gets ~1 more PA than slot 9; modeled explicitly as expected PA.
4. **Quality of contact (xBA / barrel% from the Savant snapshots)** — separates "hitting .320 with weak contact" from real form. Snapshots only exist since 5/21, so this is tested as an optional late-season feature.
5. **Home/away** — home teams skip the bottom of the 9th when leading (fewer PAs); road/home performance splits.
6. Worth knowing but **not in your free data**: opposing bullpen quality (~half of PAs come vs relievers), weather/wind, umpire zone, batter speed/BABIP profile, days of rest. Listed in the report as future upgrades.

## Architecture (all new files prefixed `Claude_`)

```
backend/Claude_data/                      ← local cache dir (created at runtime, untracked)
backend/Claude_fetch_season.py            ← fetch & cache 2026 schedule + boxscores from MLB StatsAPI
backend/Claude_build_dataset.py           ← cache → leakage-free per-batter-game feature rows (parquet/CSV in Claude_data/)
backend/Claude_train.py                   ← fit logistic regression, time-split eval, write Claude_hit_weights.json + report
backend/Claude_hit_weights.json           ← frozen coefficients (committed)
backend/Claude_hit_model.py               ← pure-Python scoring: features → P(≥1 hit); no numpy/sklearn (prod-safe)
backend/Claude_live.py                    ← async: candidate pool → Claude features → scored candidates
backend/Claude_MODEL_REPORT.md            ← findings: coefficients, metrics, comparison vs incumbent, factor write-up
backend/tests/test_Claude_hit_model.py    ← pure-math pytest
frontend/src/components/Claude_HitPicks.jsx        ← self-fetching Betting page section
frontend/src/components/Claude_HitPicks.css        ← styles (claude-hit-* classes), imported by the component
frontend/src/components/Claude_HitPicks.test.jsx   ← vitest + RTL, stubbed fetch
```

**Minimal edits to existing files (wiring only, nothing deleted):**
- `backend/main.py` — add one ~25-line endpoint `GET /betting/claude-hit-candidates` modeled exactly on `/betting/hit-candidates` (main.py:4863): call `get_betting_candidates(top_n=300, floors=0, persist_suggestions=False)`, hand the pool to `Claude_live.build_claude_candidates()`, return `ApiResponse`. No circular imports (main → Claude_live only).
- `frontend/src/components/BettingPage.jsx` — import + render `<ClaudeHitPicks onPlayerClick={...} />` as a new section (2 lines).

## Training pipeline detail

**Data source:** MLB StatsAPI (free, keyless, already used by `outcome_backtest.py`). `Claude_fetch_season.py` pulls the 2026 schedule (3/25 → yesterday, ~79 dates) and each completed game's boxscore (~1,150 calls, cached as JSON in `Claude_data/` so reruns are instant). Boxscores give what the DB lacks for history: **true batting order, actual starter, venue, home/away, and full-league coverage** (DB game logs only cover tracked players — 9.6k batter rows, 879 pitcher rows).

**Feature rows** (`Claude_build_dataset.py`), one per batter-game, every feature computed **only from data strictly before the game date**:
- `season_hit_rate_per_pa` — shrunk: `(H + k·league_rate) / (PA + k)`, k≈60
- `rolling_hit_rate_per_pa` (last 10 days), `rolling_k_pct`, `season_k_pct`
- `expected_pa` — from actual batting-order slot (same slot→PA table concept as live)
- `platoon_advantage` (0/1) — `players.bats` × starter `throws` from DB, statsapi `people` fallback (cached)
- starter pregame quality: season-to-date `h_per_bf`, `k_per_bf` (BF ≈ 3·IP + H + BB + HBP) from cached pitching lines; rolling variants
- `park_factor` — venue → `park_factors.get_park_factor()` (reused, read-only)
- `is_home`
- optional: `xba` from `hitter_savant_snapshots` (post-5/21 subset only)

**Label:** `hits ≥ 1`. **Eligibility (pregame-knowable only):** ≥30 season PA and trailing PA/game ≥ 2.5 before that date; first ~10 days of season used as burn-in history, not training rows.

**Model & eval** (`Claude_train.py`, sklearn/numpy — offline only, requirements.txt untouched):
- Logistic regression (standardized features), time-ordered split: holdout = last ~3 weeks.
- Metrics: log loss, Brier, AUC, calibration buckets, and the practical one — **top-3/5/10 picks-per-day hit rate on the holdout**, compared against (a) naive "sort by season hit rate" baseline and (b) the incumbent `hit_model.score_hit_candidate()` run on the same rows (read-only import).
- Output: coefficients + means/stds frozen to `Claude_hit_weights.json`; human-readable `Claude_MODEL_REPORT.md` with the final formula written out and a plain-English ranking of which factors mattered.

`Claude_hit_model.py` then evaluates `P = sigmoid(b0 + Σ bᵢ·zᵢ)` in pure Python with embedded fallback weights (works even if the JSON is missing), plus per-feature contribution breakdown that becomes the "reasons" list in the UI.

## Live endpoint

`Claude_live.build_claude_candidates(pool_data)`:
1. Take candidates from the existing pool (they carry `batting_order`, `bats`/`throws`, venue, `context_stats` with `season_hit_rate_per_pa`, `rolling_hit_rate_per_pa`, `hit_rolling_k_pct`, `pitcher_fip`, `pitcher_k_bb_pct`).
2. One batched DB query on `pitcher_game_logs` for opposing starters' season H/BF & K/BF; fall back to a cached statsapi season-stats call per starter (≤15/day) when the DB rows are missing, so live features match training definitions.
3. Score each with `Claude_hit_model`, attach `claude_hit: {probability, expected_pa, contributions, reasons, risks}`, sort by probability, return top N.

## Frontend section

`Claude_HitPicks.jsx` — self-contained (own fetch of `/betting/claude-hit-candidates`, own loading/error state), rendered inside BettingPage below the existing hit section. Reuses `.betting-grid`/`.betting-card`/game-grouping patterns; distinct accent (purple/violet, vs blue=edge, green=hit) via `claude-hit-*` classes in `Claude_HitPicks.css`. Cards: big P(≥1 hit), expected PA, key inputs, reason/risk chips, methodology note saying it's a season-fitted logistic model and naming the top weights.

## Tests & verification

1. `pytest backend/tests/test_Claude_hit_model.py` — sigmoid math, shrinkage, monotonicity (better form/weaker pitcher ⇒ higher P), bounds, missing-feature fallbacks.
2. Run the pipeline for real: fetch → build → train; sanity-check report numbers (league hit-incidence ≈ 60–65% for regulars; AUC meaningfully > 0.5; holdout top-5 hit rate ≥ baseline).
3. `npx vitest run` for `Claude_HitPicks.test.jsx` + existing BettingPage tests still green.
4. Preview verification (per your standing rule): backend on **port 8001**, frontend dev server, confirm the Claude section renders with live data on the Betting page, check console/network for errors, **screenshot as proof**.

## Git

Per your dismissed question I default to the safe option: build everything in the working tree on the current branch and **leave committing/PRs to you**, because main.py/BettingPage.jsx/App.css already carry uncommitted work from the other effort and I won't bundle it into a commit unreviewed. Say the word if you'd rather I branch and commit.

## Constraints honored
- Every new file starts with `Claude_`; existing files only receive small additive wiring edits.
- **No files deleted.** No new production dependencies (sklearn/numpy used only by the offline trainer).
- Existing endpoints, model files, and tests untouched.