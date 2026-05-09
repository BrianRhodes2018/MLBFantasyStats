# Plan: Betting Edge Page + Bet Audit Tracking

**Goal:** Ship a daily betting-candidates page that surfaces 5–8 hitters with the strongest combined indicators, AND a Bet Audit page that tracks how each suggestion actually performed so we can tune the scoring function over time.

**Why both at once:** Without the audit, we'd be flying blind on whether our scoring function actually picks winners. The plan treats audit as a Phase 2 follow-on, not an afterthought — recording suggestions starts on day 1 of Phase 1, even before the audit UI exists, so we don't lose a single day of data.

---

## Where we are (data foundation)

**Done and live in production:**
- Platoon advantage — `bats` / `throws` columns, populated for everyone
- Recent form — `/players/rolling-stats`, `/pitchers/rolling-stats`
- BvP history — `/matchups/vs-pitcher` (career hitter-vs-pitcher splits)
- Pitcher vulnerability — ERA, WHIP, K/9, BB/9, HR/9, FIP (FIP shipped in PR #8)
- Park factors — live from Baseball Savant, 2024–2026 rolling, 35 venues (PR #8)

**Still missing (deferred to Phase 4):**
- Weather (external API)
- Bullpen fatigue (need reliever game logs)
- Travel / getaway-day analysis (need schedule diff)

**6 of 8 signals from [MLB_Betting_Agent_Instructions.md](MLB_Betting_Agent_Instructions.md) are usable today.**

---

## Phase 1 — Betting Edge page

**Estimated effort:** 1–2 days.

### 1.1 Backend — `GET /betting/candidates`

New endpoint that orchestrates the existing data sources and returns ranked candidates.

**Inputs (query params):**
- `date` (optional, default = today, format `YYYY-MM-DD`)
- `min_pitcher_ip` (optional, default = 20) — filters out tiny-sample relievers whose FIP/K/9 are statistically meaningless
- `top_n` (optional, default = 8) — how many candidates to return

**Orchestration steps:**
1. Call `/matchups/today` to get today's games + probable pitchers + park factors (already enriched per-game).
2. For each game, identify the *opposing pitcher* for each lineup side. Skip games where either pitcher is TBD.
3. Filter out pitchers below `min_pitcher_ip`.
4. Call `/matchups/lineup/{game_id}` per game to get the projected lineup (includes handedness).
5. For each batter in each lineup:
   - **Platoon score** — bats vs. opposing throws. Bonus if opposite-handed; small penalty if same-handed.
   - **Pitcher vulnerability score** — composite of opposing pitcher's FIP, WHIP, HR/9, K-BB%. Higher = more vulnerable = better for the hitter.
   - **Recent form score** — batter's rolling 14-day OPS vs. season OPS. Bonus for hot, penalty for cold.
   - **BvP score** — call `/matchups/vs-pitcher` per lineup. Bonus when sample ≥ 10 PA AND career OPS vs this pitcher ≥ 0.900. Skip when sample is too small to trust.
   - **Park factor adjustment** — multiply final score by `runs_factor / 100`. Coors Field (117) gets 1.17×, Oracle Park (94) gets 0.94×.
6. Compute composite score, rank, return top N.

**Composite score formula (initial weights):**
```
score = 0.30 * platoon
      + 0.30 * pitcher_vulnerability
      + 0.20 * recent_form
      + 0.20 * bvp
score *= park_factor / 100
```
These weights are deliberate guesses. The whole point of the audit is to tune them.

**Response shape:**
```json
{
  "code": 200,
  "data": {
    "date": "2026-05-09",
    "candidates": [
      {
        "rank": 1,
        "player_mlb_id": 592450,
        "player_name": "Aaron Judge",
        "player_team": "Yankees",
        "game_id": 778001,
        "opposing_pitcher_mlb_id": 666749,
        "opposing_pitcher_name": "Blake Snell",
        "venue": "Yankee Stadium",
        "composite_score": 87.2,
        "signals": {
          "platoon": {"value": 1.0, "fired": true, "detail": "RHH vs LHP"},
          "pitcher_vulnerability": {"value": 0.78, "fired": true, "detail": "FIP 4.92, WHIP 1.45"},
          "recent_form": {"value": 0.85, "fired": true, "detail": ".342 / 1.020 OPS last 14 days"},
          "bvp": {"value": 0.65, "fired": true, "detail": "11 PA, .455 / 1.182 OPS career vs Snell"},
          "park_factor": {"value": 1.04, "fired": true, "detail": "Yankee Stadium runs 104"}
        },
        "summary": "RHH vs vulnerable LHP at hitter-friendly Yankee Stadium; hot last two weeks and historically owns this matchup."
      },
      ...
    ]
  }
}
```

**Files:**
- New `backend/betting.py` — scoring functions (pure, easily testable)
- `backend/main.py` — new endpoint, imports from betting.py
- New `backend/tests/test_betting.py` — unit tests for scoring functions (per the testing plan in [PLAN_AUTH_AND_TESTING.md](PLAN_AUTH_AND_TESTING.md))

### 1.2 Persist suggestions on generation (foundation for audit)

**Critical:** the moment Phase 1 ships, every call to `/betting/candidates` should also write the result to a `bet_suggestions` table — even though the audit UI isn't built yet. This way Phase 2 has historical data to audit from day 1 instead of starting from zero.

**Schema (bet_suggestions):**
```
id              SERIAL PRIMARY KEY
suggested_date  DATE        -- the day the suggestion is for
generated_at    TIMESTAMP   -- when /betting/candidates was called
rank            INTEGER     -- 1..top_n
player_mlb_id   INTEGER
player_name     VARCHAR(100)
player_team     VARCHAR(50)
game_id         INTEGER
opposing_pitcher_mlb_id INTEGER
opposing_pitcher_name   VARCHAR(100)
venue           VARCHAR(100)
composite_score FLOAT
signals_json    TEXT        -- the full signals object as JSON
summary         VARCHAR(500)
-- Audit fields (filled later by Phase 2 backfill):
actual_at_bats       INTEGER
actual_hits          INTEGER
actual_doubles       INTEGER
actual_triples       INTEGER
actual_home_runs     INTEGER
actual_total_bases   INTEGER
actual_rbi           INTEGER
actual_runs          INTEGER
actual_walks         INTEGER
actual_strikeouts    INTEGER
actual_recorded_at   VARCHAR(30)  -- ISO timestamp; null until backfilled
```

**Idempotency:** unique constraint on `(suggested_date, rank)` so re-calling the endpoint for the same date overwrites (or noops). The `top_n` shouldn't waver intra-day, but we should be defensive.

### 1.3 Frontend — `/betting` page

- New nav link below the existing "Today's Pitching Matchups" link
- New `BettingPage.jsx` component that fetches `/betting/candidates`
- Renders 5–8 cards in the [MLB_Betting_Agent_Instructions.md](MLB_Betting_Agent_Instructions.md) output template
- Each card:
  - Player name (clickable → existing PlayerModal)
  - Composite score badge
  - Signal chips: ✓ for fired, dim for not fired, hover for detail
  - One-line summary
  - "Last updated" timestamp (we already have the banner pattern)
- Optional: collapsible "Show signal math" accordion per card so the user can see exactly how a score was built

**Files:**
- `frontend/src/components/BettingPage.jsx`
- `frontend/src/App.jsx` — add view state, nav link
- `frontend/vite.config.js` — proxy `/betting`
- `frontend/src/App.css` — card styles

**Exit criteria for Phase 1:**
- `/betting/candidates?date=...` returns 5–8 ranked candidates with full signal breakdown
- Frontend renders the cards, clicking a name opens the existing PlayerModal
- Every call writes a row per candidate into `bet_suggestions` (even though no audit UI exists yet)
- Unit tests on the scoring function pass

---

## Phase 2 — Bet Audit page (the new ask)

**Estimated effort:** 1–2 days.

This is the tracker — what we suggested, what actually happened, are we any good.

### 2.1 Backfill actual results

**Where:** new phase added to `daily_update.py` after the existing 4 phases.

**Logic:**
1. Query `bet_suggestions` where `actual_recorded_at IS NULL AND suggested_date < today`. These are completed games we haven't pulled actuals for.
2. Group by `(player_mlb_id, suggested_date)` — one MLB API call per player per game.
3. For each, hit `statsapi.get('people', {personIds: ..., hydrate: 'stats(group=hitting,type=gameLog)...'})` filtered to that game date.
4. Extract AB, H, 2B, 3B, HR, TB, RBI, R, BB, SO from that game's stat line.
5. Update the row.

**Resilience:** if the API call fails or the player didn't actually play (sat out, called up later, etc.), set `actual_recorded_at` to "skipped" rather than null so we don't keep retrying forever. Track the reason in a `actual_skip_reason` column.

### 2.2 Backend — `GET /betting/audit`

**Inputs (query params):**
- `from` (default = 30 days ago) — start date
- `to` (default = today) — end date
- `signal` (optional) — filter to suggestions where this specific signal fired (e.g. `signal=platoon`)
- `min_score` (optional) — filter to only high-confidence suggestions

**Response:** array of historical suggestions with actuals filled in, plus an aggregate summary block.

**Aggregates we want:**
- Total suggestions in window
- % with backfilled actuals (data freshness indicator)
- Hit rate by definition: % of suggestions where the player recorded ≥ 1 extra-base hit
- Hit rate by another definition: % where total bases ≥ 2
- Avg total bases per suggestion vs. season avg per suggestion (lift metric)
- Per-signal hit rate: "platoon-only suggestions hit 58%, BvP-only suggestions hit 51%, all-four-fired suggestions hit 71%"

**Why multiple "hit rate" definitions:** without an actual sportsbook prop bet, "did the bet hit" is ambiguous. We track several common interpretations so we can pick whichever correlates best with our scoring function and then iterate on it.

### 2.3 Frontend — `/audit` page

- New nav link "Bet Audit" alongside "Betting Edge"
- New `BetAuditPage.jsx` with:
  - **Aggregate summary band** at top: hit rates, lift metric, total suggestions, freshness percentage
  - **Per-signal breakdown** chart or table — "which signal is actually predictive?"
  - **Suggestion log table** — one row per (date, player, rank), columns: date | player | score | signals fired | actual line (AB-H-2B-3B-HR-RBI) | hit/miss flag
  - **Date range picker** + signal filter
  - Click a row → existing PlayerModal for that player

The per-signal breakdown is the single most valuable view — it's the feedback loop that lets you adjust composite-score weights.

### 2.4 Persist + audit working together

After Phase 2 lands, the lifecycle is:
```
Day N at 00:00     → /betting/candidates called (manual or scheduled), writes 5-8 rows
Day N+1 at 06:00   → daily-update phase 5 runs, populates actuals for Day N's rows
Day N+1 onwards    → /audit page reflects Day N's results
```

**Exit criteria for Phase 2:**
- Yesterday's suggestions show actual stat lines on the audit page
- Aggregate hit rates render correctly
- Per-signal breakdown lets us see which signals actually predict hits

---

## Phase 3 — Tune the scoring function based on audit data

**Estimated effort:** Ongoing. First pass after ~2 weeks of audit data.

This is where the Phase 2 audit pays off. Once we have a few weeks of historical data we can:

1. **Re-weight signals** — if BvP only hits 45% but Platoon hits 62%, drop BvP weight and boost Platoon.
2. **Add interaction terms** — maybe "Platoon AND Park" is much stronger than either alone. The audit data will reveal it.
3. **Filter low-signal candidates out** — if composite scores under 60 hit 30% but scores above 80 hit 70%, set a minimum threshold.
4. **Add new signals** — if we notice a third-party indicator that correlates (e.g. recent home/away splits), add it.

Each adjustment is a small PR with rationale citing the audit metrics.

**Optional helper:** add a `/betting/audit/correlate` endpoint that runs a simple linear regression of `composite_score` vs. `actual_total_bases` (or similar outcome variable) over the audit window and returns the R² so we know if the function is improving overall.

---

## Phase 4 — Fill the missing 2-of-8 signals

**Estimated effort:** 2–4 days each, easily parallelized.

These were deferred from the foundation work. With audit infrastructure in place, we can A/B them — ship the new signal behind a flag, see if it improves audit hit rate, decide whether to keep it.

### 4.1 Weather
- Source: OpenWeatherMap, NOAA, or similar (most have a free tier)
- Pull wind direction + speed + temp for each game's venue
- Wind blowing out + hot temp = boost hitter scores
- Wind blowing in = penalize hitter scores

### 4.2 Bullpen fatigue
- We already have `pitcher_game_logs` for everyone now (HBP added in PR #8)
- For each team's relievers, count appearances in the last 3 days
- If a team's top 2 relievers each pitched 2 of the last 3 days, opposing offense gets a "late-game soft bullpen" bonus
- Affects late-game scoring more than early — maybe a smaller boost spread across the lineup

### 4.3 Travel / getaway day
- MLB schedule is public via the Stats API
- For each road team, check if their previous game was in a different time zone AND was a night game
- If yes, "getaway day" penalty on that team's hitters
- Modest weight; this is the spec's smallest signal

After all three are integrated, the scoring function uses 8/8 signals. The audit page shows which ones are actually moving the needle.

---

## Open design questions (decide before / during Phase 1)

1. **Do we auto-generate suggestions on a schedule, or only on demand?** Auto means 100% audit coverage but burns the MLB API on days nobody looks. On demand means missing days. *Recommend: trigger from `daily_update.py` so it ties into the existing workflow — same cron, same reliability, automatic backfill.*

2. **Should suggestions be locked once generated, or regenerated if you reload the page?** *Recommend: lock to whatever was generated first that day, so the audit reflects what was actually surfaced to the user.*

3. **How do we handle pitchers being scratched late (TBD → actual)?** *Recommend: re-run the candidate generation if any starter is updated, but flag the row as "regenerated" in the audit so we don't double-count outcomes.*

4. **What's the "win" definition for a suggestion?** *Recommend: track multiple — TB ≥ 2, ≥ 1 XBH, fantasy-points percentile rank — let the audit page show all three, pick whichever correlates best with our scoring after a few weeks.*

5. **Where does suggestion generation actually live?** *Recommend: add a `--generate-suggestions` flag on `daily_update.py` that calls into the betting service. Easy to schedule, easy to run manually for testing.*

---

## Suggested execution order

1. **Phase 1** scoring functions + endpoint + frontend page (1–2 days)
2. **Phase 1.2** persistence to `bet_suggestions` (lands with Phase 1, no separate PR)
3. **Auto-generation** wired into `daily_update.py` so suggestions log every day (small commit)
4. **Phase 2** backfill + audit endpoint + audit page (1–2 days)
5. Run for **2 weeks** of data accumulation
6. **Phase 3** first pass of scoring tune-up
7. **Phase 4** signals as separate PRs, each A/B tested through audit data

---

*Plan generated 2026-05-09. Will be revisited at the start of each phase.*
