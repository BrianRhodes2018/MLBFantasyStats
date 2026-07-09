# Staging Environment (Neon branch)

Staging is a disposable, production-shaped copy of the app used to test
risky changes — especially **database migrations** — before they touch
the real thing. Ours is built on Neon's branching: a staging database
is an instant copy-on-write snapshot of production (same mechanism as
the frozen 2025 season snapshot), so it costs nothing to create,
nothing meaningful to store, and can be reset at any time.

## One-time setup

1. In the [Neon console](https://console.neon.tech): your project →
   **Branches** → **Create branch**
   - Name: `staging`
   - Parent: `main` (or `production` — whichever branch the app's
     `DATABASE_URL` points at)
2. Copy the new branch's **connection string** and add it to
   `backend/.env`:

   ```
   STAGING_DATABASE_URL=postgres://...staging-endpoint...neon.tech/neondb?sslmode=require
   ```

Because the branch is a byte-for-byte copy, it arrives already carrying
the `alembic_version` bookmark — Alembic treats it exactly like
production, which is the point.

## Testing a schema change against staging (the main use case)

Before merging a PR that contains a new migration:

```powershell
cd backend
$env:ALEMBIC_DATABASE_URL = (Select-String -Path .env -Pattern '^STAGING_DATABASE_URL=(.+)$').Matches[0].Groups[1].Value
python -m alembic upgrade head       # migration runs against staging first
Remove-Item Env:ALEMBIC_DATABASE_URL
```

If the migration fails or mangles data, it failed on a throwaway copy —
delete the branch, fix the migration, re-create the branch, retry.
Production never saw a thing.

## Running the app against staging

To exercise endpoints against production-shaped data without touching
production:

```powershell
cd backend
$env:DATABASE_URL = (Select-String -Path .env -Pattern '^STAGING_DATABASE_URL=(.+)$').Matches[0].Groups[1].Value
python -m uvicorn main:app --port 8001
Remove-Item Env:DATABASE_URL     # afterwards, in the same shell
```

Startup runs `run_migrations()` against staging (fine — that's the
rehearsal), and the frontend dev server on 5173 proxies to it as usual.

## Refreshing staging

A branch drifts from production as both change. When staging data feels
stale, don't maintain it — **replace it**: delete the `staging` branch
in the Neon console and create it again from the parent. Seconds of
work, and it also discards any junk that tests wrote.

## What staging is NOT for

- The daily hit-picks task writes to production via its restricted
  `hit_picks_writer` role — that flow is already low-risk and does not
  go through staging.
- Frontend-only changes: Vercel automatically builds a preview for
  every PR; that preview (pointed at the production API) is usually
  enough. Staging earns its keep when the **database or backend
  behavior** changes.
