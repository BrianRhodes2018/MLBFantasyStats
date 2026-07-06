# ===========================================================================
# run_daily_hit_picks.ps1 - Daily hit-model runner (Windows Task Scheduler)
# ===========================================================================
#
# Runs the two-step daily workflow for the hit prediction model:
#   1. grade_hit_picks.py    - grade yesterday's saved picks against real
#                              boxscores; update the per-model-version ledger
#   2. predict_hits_today.py - train hit_gbm_v2 on all data through yesterday
#                              and save today's ranked pick list
#
# Register with Task Scheduler (one-time, run from any PowerShell):
#   $action  = New-ScheduledTaskAction -Execute "powershell.exe" `
#     -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$PSScriptRoot\run_daily_hit_picks.ps1`""
#   $trigger = New-ScheduledTaskTrigger -Daily -At 9:00AM
#   Register-ScheduledTask -TaskName "MLB Daily Hit Picks" -Action $action -Trigger $trigger
#
# Notes:
#   - 9 AM local: yesterday's boxscores are final, today's slate is posted.
#     Lineups are projected until officials post; the picks endpoint always
#     serves whatever the latest run produced.
#   - Output goes to backend/logs/hit_picks_daily.log (gitignored).
#   - The boxscore cache is shared with the main checkout so nothing is
#     ever downloaded twice, regardless of which checkout this runs from.
# ===========================================================================

$ErrorActionPreference = "Continue"
$backendDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# Prefer the Anaconda interpreter this project uses; fall back to PATH.
$python = "C:\Users\brhod\anaconda3\python.exe"
if (-not (Test-Path $python)) { $python = "python" }

# Shared boxscore cache lives in the primary checkout. Fall back to the
# script-relative default if this copy IS the primary checkout.
$cacheDir = "C:\Users\brhod\Brian\FastAPI-Polars-React-MLB\backend\.backtest_cache"
if (-not (Test-Path $cacheDir)) { $cacheDir = Join-Path $backendDir ".backtest_cache" }

$logDir = Join-Path $backendDir "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir "hit_picks_daily.log"

Add-Content $log "`n=== hit picks daily run: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ==="

Add-Content $log "--- step 1: grade yesterday's picks ---"
& $python (Join-Path $backendDir "grade_hit_picks.py") --cache-dir $cacheDir 2>&1 | Add-Content $log

Add-Content $log "--- step 2: generate today's picks ---"
& $python (Join-Path $backendDir "predict_hits_today.py") --cache-dir $cacheDir 2>&1 | Add-Content $log

Add-Content $log "=== done: $(Get-Date -Format 'HH:mm:ss') ==="
