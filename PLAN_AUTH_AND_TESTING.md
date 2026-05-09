# Plan: Authentication + Fantasy Scoring Tests

**Goal:** Get the codebase ready to safely add MLB Betting features by locking down user identity and pinning down fantasy scoring math behind tests.

**Why this order:** Auth is the seam every future feature lives on (lineups, bets, wallets all attach to a `user_id`). Tests are the safety net that lets us refactor `main.py` (3,139 lines) and `App.jsx` (1,054 lines) without breaking what works. Doing both first makes every later feature cheaper and safer.

---

## Phase 1 — Test infrastructure for fantasy scoring math

**Estimated effort:** half a day to a day. Lowest risk first; nothing in this phase touches running code.

### Step 1.1 — Add test dependencies
- Add to `backend/requirements.txt` (or split into `requirements-dev.txt`):
  - `pytest`
  - `pytest-asyncio`
  - `pytest-cov` (optional, for coverage reports)

### Step 1.2 — Create the test directory
```
backend/
  tests/
    __init__.py
    conftest.py              # shared fixtures (sample DataFrames, sample scoring dicts)
    test_espn_fantasy.py     # tests for compute_fantasy_points_batters/pitchers
    test_yahoo_fantasy.py    # tests for compute_yahoo_fantasy_points_batters/pitchers
```

### Step 1.3 — Write the first batch of tests
Target file: [backend/espn_fantasy.py](backend/espn_fantasy.py)

Cases to cover (start with these eight, expand later):

| Test | What it pins down | Where the logic lives |
|---|---|---|
| `test_outs_conversion_handles_baseball_notation` | `205.0 → 615 outs`, `205.1 → 616`, `205.2 → 617` | [espn_fantasy.py:437-449](backend/espn_fantasy.py:437) |
| `test_singles_computed_from_hits_minus_extra_base` | Aaron Judge's singles given his real 2B/3B/HR | [espn_fantasy.py:319-330](backend/espn_fantasy.py:319) |
| `test_xbh_is_doubles_plus_triples_plus_hr` | XBH math | [espn_fantasy.py:333-343](backend/espn_fantasy.py:333) |
| `test_obp_handles_zero_at_bats` | No division-by-zero | [espn_fantasy.py:348-361](backend/espn_fantasy.py:348) |
| `test_obp_handles_null_walks_and_hbp` | Old data with missing columns | [espn_fantasy.py:348-361](backend/espn_fantasy.py:348) |
| `test_negative_scoring_categories` | K = -1pt yields negative contribution | [espn_fantasy.py:367-371](backend/espn_fantasy.py:367) |
| `test_unknown_stat_id_is_skipped` | League scores a stat we don't store — doesn't crash | [espn_fantasy.py:309-316](backend/espn_fantasy.py:309) |
| `test_empty_dataframe_returns_empty_with_fantasy_pts_column` | Edge case | [espn_fantasy.py:296-297](backend/espn_fantasy.py:296) |

Plus one **integration-style golden test** per provider:
- `test_logan_webb_pitcher_points_match_espn_app` — pick a real pitcher from a real league, hardcode the expected total points, compare exactly. This is the single most valuable test in the file because it catches *any* regression in the pipeline.

### Step 1.4 — Add a PR check workflow
Create `.github/workflows/pr-checks.yml`:
- Trigger: `on: pull_request`
- Run: `pip install -r requirements.txt && pytest backend/tests/`
- Optional: also run `npm run build` for the frontend so build breakage doesn't ship to Vercel

### Step 1.5 — Document the test approach
Short `backend/tests/README.md` explaining:
- How to run tests locally: `pytest backend/tests/ -v`
- Convention: any change to `espn_fantasy.py` or `yahoo_fantasy.py` requires a corresponding test

**Exit criteria for Phase 1:** CI runs on every PR, all eight tests pass, the golden Logan Webb test passes against a real league config.

---

## Phase 2 — Authentication

**Estimated effort:** one to two days for the basics, longer if rolling our own.

### Step 2.1 — Pick an auth provider (decide before coding)
Recommended:
- **Clerk** — easiest, beautiful UI, free up to 10k users. Drop-in React components.
- **Auth0** — most mature, best ecosystem, free up to 7.5k users.
- **Supabase Auth** — only if we plan to move the DB to Supabase too.
- **FastAPI-Users** — only if we want to fully own the stack and accept the maintenance burden.

**Recommendation:** Clerk. Lowest friction for a learning context, built-in MFA, magic links, social login, and email verification.

### Step 2.2 — Add the `users` table
New file: `backend/migrations/` (introducing Alembic — more on this below) or extend [backend/migrations.py](backend/migrations.py) for now.

Schema:
```
users (
  id              SERIAL PRIMARY KEY,
  external_id     VARCHAR(255) UNIQUE,   -- Clerk/Auth0 user ID
  email           VARCHAR(255) UNIQUE NOT NULL,
  created_at      TIMESTAMP NOT NULL DEFAULT NOW(),
  updated_at      TIMESTAMP NOT NULL DEFAULT NOW(),
  -- Future: kyc_status, dob, geo_state, etc. (for betting)
)
```

### Step 2.3 — Add a `get_current_user` dependency
New file: `backend/auth.py`

Responsibilities:
- Read the `Authorization: Bearer <jwt>` header
- Verify the JWT against the auth provider's JWKS endpoint
- Look up (or create) the matching row in `users`
- Return the `User` object to the endpoint via `Depends(get_current_user)`

Keep it small — the heavy lifting (token issuance, refresh, MFA) is in Clerk's hands.

### Step 2.4 — Add `user_id` to `fantasy_leagues`
Migration:
- `ALTER TABLE fantasy_leagues ADD COLUMN user_id INTEGER REFERENCES users(id)`
- Backfill existing rows to a single `system` user (or just to your own user id if it's only your data)
- After backfill: `ALTER COLUMN user_id SET NOT NULL`

### Step 2.5 — Lock down `/fantasy/*` endpoints
Update each fantasy endpoint in [backend/main.py](backend/main.py) to:
1. Take `current_user: User = Depends(get_current_user)` as a parameter
2. Add `WHERE fantasy_leagues.c.user_id == current_user.id` to every query
3. On `POST /fantasy/leagues`, set `user_id=current_user.id` on insert

Endpoints to update (lines from your code):
- [POST /fantasy/leagues](backend/main.py:1790)
- [GET /fantasy/leagues](backend/main.py:1938)
- [DELETE /fantasy/leagues/{league_db_id}](backend/main.py:1977)
- [GET /fantasy/points/batters/{league_db_id}](backend/main.py:2018)
- [GET /fantasy/points/pitchers/{league_db_id}](backend/main.py:2102)
- [POST /fantasy/yahoo/auth-url](backend/main.py:2549)

### Step 2.6 — Decide on the rest of the endpoints
For the read-only stat endpoints (`/players/`, `/pitchers/`, `/matchups/today`, etc.), pick one of:
- **Open** (no auth required) — simplest, but exposes the API to scraping
- **Soft auth** — works without a user but offers personalization with one
- **Required auth** — every endpoint behind `Depends(get_current_user)`

**Recommendation:** soft auth on read-only stat endpoints, required auth on everything mutating or user-owned. Add rate limiting (Phase 4) to compensate on the open paths.

### Step 2.7 — Update the frontend
- Install Clerk's React SDK (or equivalent)
- Wrap `<App />` in `<ClerkProvider>` in [frontend/src/main.jsx](frontend/src/main.jsx)
- Add a sign-in/sign-up gate around the dashboard
- Update `safeFetch` in [frontend/src/App.jsx:181](frontend/src/App.jsx:181) to attach the `Authorization: Bearer` header

**Exit criteria for Phase 2:** Logging in returns only that user's connected leagues; deleting another user's league returns 404; CI tests still pass.

---

## Phase 3 — Encrypt existing tokens at rest

**Estimated effort:** half a day.

### Step 3.1 — Generate a master key
- Add `MASTER_ENCRYPTION_KEY` to `.env.example` (placeholder) and to Render's environment variables (real value)
- Use `cryptography.fernet.Fernet.generate_key()` once locally to produce the value

### Step 3.2 — Add encrypt/decrypt helpers
New file: `backend/crypto.py`
- `encrypt(plaintext: str) -> str`
- `decrypt(ciphertext: str) -> str`

### Step 3.3 — Migrate existing rows
One-time script `backend/scripts/encrypt_existing_tokens.py`:
- Read all rows from `fantasy_leagues`
- Encrypt `espn_s2`, `swid`, `yahoo_access_token`, `yahoo_refresh_token`
- Write back

### Step 3.4 — Update read/write paths
Update [backend/main.py](backend/main.py) and [backend/yahoo_fantasy.py](backend/yahoo_fantasy.py) to:
- Encrypt on insert
- Decrypt on read just before passing to the ESPN/Yahoo APIs
- Never log decrypted values

**Exit criteria for Phase 3:** Tokens in the DB are unreadable to anyone without the master key. App still works end-to-end.

---

## Phase 4 — Foundation hardening (do alongside or after Phase 2)

These aren't strictly part of "auth + tests" but they multiply the value of both:

- **Move to Alembic for migrations** — your hand-rolled [backend/migrations.py](backend/migrations.py) only adds columns. Betting will need column drops, renames, and data migrations. Do this *before* the schema gets bigger.
- **Add `slowapi` rate limiting** — protects open endpoints from scraping/DoS once Phase 2 leaves any endpoints public.
- **Add Sentry** — install on backend and frontend. ~30 min of work, catches every silent error you currently never see in production.
- **Tighten CORS** — replace `allow_methods=["*"]` and `allow_headers=["*"]` with explicit lists.

---

## What this unlocks

After Phases 1–3 are done, the betting roadmap becomes possible:

- A `bets` table with `user_id` FK is one migration away
- Bet settlement math has the same shape as fantasy scoring — same testing infrastructure applies
- Wallets, KYC fields, and geo-fencing all have a stable user identity to attach to
- We can refactor `main.py` into routers without fearing math regressions
- We can add real-time score updates without worrying which user's data we're touching

## What this does NOT cover (separate plans needed later)

- **Compliance for the betting side** — geo-fencing, KYC, age verification, state licensing, responsible gambling features. This has long lead times — start the legal/regulatory conversation in parallel with Phase 1.
- **Refactoring `main.py` and `App.jsx`** — best done after Phase 1 (so tests catch regressions) but before betting features land.
- **Real-time/WebSocket layer** for live games.
- **Caching layer** (Redis).
- **TypeScript migration** on the frontend.

---

## Suggested execution order

1. Phase 1 (tests) — start here, lowest risk, biggest leverage
2. Phase 2.1–2.3 (auth provider + users table + dependency) in parallel with Phase 4 (Alembic, Sentry)
3. Phase 2.4–2.7 (lock down `/fantasy/*` + frontend integration)
4. Phase 3 (encrypt tokens)
5. Phase 4 cleanups (rate limiting, CORS tightening)
6. Refactor `main.py` into routers (now safe because tests exist)
7. Begin betting domain work

---

*Plan generated 2026-05-09. Revisit when ready to execute.*
