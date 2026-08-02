# Pitcher Ks staging runbook

This runbook connects the already-protected Vercel Preview for
`codex/pitcher-ks-results` to a disposable Render API backed by the existing
Neon staging branch. It does not modify the production Render service,
production Vercel environment variables, or production database.

## Safety boundaries

- Work only from `codex/pitcher-ks-results`.
- In Render, import `render.staging.yaml`, not `render.yaml`.
- Before approving the Blueprint, confirm its change list contains exactly one
  new service named `mlb-stats-api-pitcher-ks-staging` and no changed resources.
- Set the staging service's `DATABASE_URL` from `STAGING_DATABASE_URL` in the
  local `backend/.env`. Never use the production URL.
- Keep `autoDeployTrigger: off`; every staging backend deploy is manual.
- In Vercel, scope `VITE_API_URL` to Preview plus the
  `codex/pitcher-ks-results` Git branch. Do not select Production.
- Leave Vercel Deployment Protection enabled.

## Current rehearsal state

The Neon staging branch is migrated to Alembic head `c8a4e7d2f610` and contains
a complete August 1, 2026 Pitcher Ks bundle: 30 probable starters for
each of `decomposed`, `count`, and `bayes`, all sharing one frozen comparison
cohort. The committed model artifact passed its SHA-256 integrity check before
those rows were written. The grading rehearsal confirmed 29 starters and one
DNS across all three approaches; an immediate rerun produced zero writes.

## 1. Create the isolated Render service

1. Sign in to Render and choose **New > Blueprint**.
2. Select `BrianRhodes2018/MLBFantasyStats` and the
   `codex/pitcher-ks-results` branch.
3. Set **Blueprint Path** to `render.staging.yaml`.
4. Review the proposed resources against the safety boundaries above.
5. Set these prompted environment variables:
   - `DATABASE_URL`: the local `STAGING_DATABASE_URL` value.
   - `CORS_ORIGINS`: the protected Vercel branch alias and current generated
     Preview URL, comma-separated.
6. Deploy the Blueprint and record the new HTTPS `onrender.com` URL.

No paid data provider is required. The service uses the existing Neon staging
branch, a Render free web instance, the existing Vercel Preview, the committed
model artifact, and MLB's public Stats API inputs. `SPORTSDATAIO_API_KEY` is
intentionally omitted; the application already has a free MLB fallback.

## 2. Verify the staging API before wiring Vercel

All seven requests must return HTTP 200. Each projection response and the
comparison response must contain 30 rows for `2026-08-01`.

```powershell
$stagingApi = "https://<staging-service>.onrender.com"
Invoke-RestMethod "$stagingApi/health"
Invoke-RestMethod "$stagingApi/api/pitcher-ks/approaches/decomposed/latest"
Invoke-RestMethod "$stagingApi/api/pitcher-ks/approaches/count/latest"
Invoke-RestMethod "$stagingApi/api/pitcher-ks/approaches/bayes/latest"
Invoke-RestMethod "$stagingApi/api/pitcher-ks/compare/latest"
Invoke-RestMethod "$stagingApi/api/pitcher-ks/approaches/decomposed/dates"
Invoke-RestMethod "$stagingApi/api/pitcher-ks/ledger"
```

Do not continue if `/health` fails, the database revision is not
`c8a4e7d2f610`, any approach is empty, or the comparison cohort is incomplete.

## 2a. Rehearse official result confirmation

Run the grading command against staging without `--apply` first:

```powershell
$env:DATABASE_URL = $env:STAGING_DATABASE_URL
uv run --locked --no-dev python grade_pitcher_ks.py --date 2026-08-01
```

Review the status counts and confirm that every unique pitcher-game is matched.
Then rerun with `--apply`. A second dry run must report zero changed rows. Confirm
that all three approach rows for each pitcher contain the same official result.

## 3. Point only the Pitcher Ks Preview branch at staging

1. Sign in to Vercel and open project `mlb-fantasy-stats-q63c`.
2. Under **Settings > Environment Variables**, add or update `VITE_API_URL`.
3. Select **Preview**, narrow it to Git branch
   `codex/pitcher-ks-results`, and enter the staging Render URL.
4. Confirm **Production is not selected**.
5. Redeploy the latest reviewed commit from the branch so the Vite build
   receives the new Preview-only value.

The Pitcher Ks navigation is already enabled automatically for Vercel Preview
builds. Production remains dark unless `VITE_PITCHER_KS_ENABLED=true` is
explicitly added to Production, which is outside this staging procedure.

## 4. Acceptance check

Open the protected Preview while signed in and verify:

1. The stats dashboard and Hit Picks V2 still load.
2. **Pitcher Ks > 1 · Simulation** shows 30 rows.
3. **2 · Count ML** shows 30 rows.
4. **3 · Empirical Bayes** shows 30 rows.
5. **Compare** shows 30 paired starters from one frozen cohort.
6. The history calendar can reload August 1 without changing frozen inputs.
7. Final games show Actual Ks, signed error, and a Final/DNS status.
8. The comparison shows per-model errors and the closest approach.
9. The glossary expands with keyboard and pointer input.
10. The browser console has no CORS, failed-fetch, or JavaScript errors.
11. The Render logs show requests only against the staging service.

## Rollback

If any acceptance check fails:

1. Remove the branch-scoped `VITE_API_URL` or redeploy the previous Preview.
2. Suspend the Render staging service.
3. Leave production untouched and diagnose against Neon staging.

Deleting the Render staging service or replacing the Neon staging branch is
safe only after confirming neither production service references it.
