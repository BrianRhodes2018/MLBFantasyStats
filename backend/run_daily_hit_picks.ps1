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
#   $t1 = New-ScheduledTaskTrigger -Daily -At 7:30AM   # early board (projections)
#   $t2 = New-ScheduledTaskTrigger -Daily -At 2:00PM   # refresh with official lineups
#   Register-ScheduledTask -TaskName "MLB Daily Hit Picks" -Action $action -Trigger $t1, $t2
#
# Notes:
#   - 7:30 AM local: yesterday's boxscores are final, today's slate is
#     posted, and there's alerting margin before the earliest day games.
#     Lineups are mostly projected this early.
#   - 2:00 PM local: re-scores the slate once most official lineups are
#     posted (pick storage is idempotent — the page updates in place).
#   - Output goes to backend/logs/hit_picks_daily.log (gitignored).
#   - The boxscore cache is shared with the main checkout so nothing is
#     ever downloaded twice, regardless of which checkout this runs from.
# ===========================================================================

$ErrorActionPreference = "Continue"
$backendDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# Use only the environment created from backend/uv.lock. Never fall back to an
# arbitrary PATH interpreter: an invalid runtime must fail before publishing.
$python = Join-Path $backendDir ".venv\Scripts\python.exe"
$smokeCheck = Join-Path $backendDir "scripts\check_ml_environment.py"
$logicalCpus = [Environment]::ProcessorCount
$env:LOKY_MAX_CPU_COUNT = [Math]::Max(1, $logicalCpus - 1).ToString()

# Shared boxscore cache lives in the primary checkout. Fall back to the
# script-relative default if this copy IS the primary checkout.
$cacheDir = "C:\Users\brhod\Brian\FastAPI-Polars-React-MLB\backend\.backtest_cache"
if (-not (Test-Path $cacheDir)) { $cacheDir = Join-Path $backendDir ".backtest_cache" }

$logDir = Join-Path $backendDir "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir "hit_picks_daily.log"
if ((Test-Path $log) -and (Get-Item $log).Length -gt 5MB) {
    Move-Item -LiteralPath $log -Destination ($log + ".1") -Force
}

# ---------------------------------------------------------------------------
# Dead-man's-switch monitoring (healthchecks.io)
# ---------------------------------------------------------------------------
# HIT_PICKS_HEALTHCHECK_URL in backend/.env holds the check's ping URL.
# We signal /start when the run begins, a plain ping on success, and
# /fail (with the log tail attached) when either step errors. If the
# success ping never arrives — task didn't run, PC was off, script broke
# before pinging — healthchecks.io emails after its grace period.
# When the variable is unset, monitoring is simply skipped.

$hcUrl = $null
$envFile = Join-Path $backendDir ".env"
if (Test-Path $envFile) {
    $match = Select-String -Path $envFile -Pattern '^HIT_PICKS_HEALTHCHECK_URL=(.+)$' | Select-Object -First 1
    if ($match) { $hcUrl = $match.Matches[0].Groups[1].Value.Trim() }
}

function Send-Healthcheck([string]$suffix, [string]$body) {
    if (-not $hcUrl) { return }
    try {
        Invoke-RestMethod -Method Post -Uri ($hcUrl + $suffix) -Body $body -TimeoutSec 15 | Out-Null
    } catch {
        Add-Content $log "warning: healthcheck ping '$suffix' failed: $_"
    }
}

Send-Healthcheck "/start" ""
Add-Content $log "`n=== hit picks daily run: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ==="

if (-not (Test-Path $python)) {
    $message = "preflight failed: locked environment is missing at $python"
    Add-Content $log $message
    Send-Healthcheck "/fail" $message
    exit 1
}

Add-Content $log "--- preflight: validate locked ML environment ---"
& $python $smokeCheck --json 2>&1 | Add-Content $log
$preflightExit = $LASTEXITCODE
if ($preflightExit -ne 0) {
    $tail = (Get-Content $log -Tail 25) -join "`n"
    Send-Healthcheck "/fail" "preflight=$preflightExit`n`n$tail"
    exit 1
}

Add-Content $log "--- step 1: grade yesterday's picks ---"
& $python (Join-Path $backendDir "grade_hit_picks.py") --cache-dir $cacheDir 2>&1 | Add-Content $log
$gradeExit = $LASTEXITCODE

Add-Content $log "--- step 2: generate today's picks ---"
& $python (Join-Path $backendDir "predict_hits_today.py") --cache-dir $cacheDir 2>&1 | Add-Content $log
$predictExit = $LASTEXITCODE

Add-Content $log "=== done: $(Get-Date -Format 'HH:mm:ss') (grade=$gradeExit, predict=$predictExit) ==="

if ($gradeExit -eq 0 -and $predictExit -eq 0) {
    Send-Healthcheck "" "grade=$gradeExit predict=$predictExit"
    exit 0
} else {
    $tail = (Get-Content $log -Tail 25) -join "`n"
    Send-Healthcheck "/fail" "grade=$gradeExit predict=$predictExit`n`n$tail"
    exit 1
}
