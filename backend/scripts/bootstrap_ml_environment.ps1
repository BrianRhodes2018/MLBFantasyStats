# Build and validate the project-local Python environment from uv.lock.
$ErrorActionPreference = "Stop"

$backendDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$pythonVersion = (Get-Content (Join-Path $backendDir ".python-version") -Raw).Trim()
$python = Join-Path $backendDir ".venv\Scripts\python.exe"
$smokeCheck = Join-Path $backendDir "scripts\check_ml_environment.py"
$uvCacheDir = Join-Path $backendDir ".uv-cache"
$uv = Get-Command uv -ErrorAction SilentlyContinue

if (-not $uv) {
    throw "uv is required. Install it from https://docs.astral.sh/uv/getting-started/installation/ and rerun this script."
}

Push-Location $backendDir
try {
    & $uv.Source --cache-dir $uvCacheDir sync --locked --managed-python --python $pythonVersion
    if ($LASTEXITCODE -ne 0) { throw "uv sync failed with exit code $LASTEXITCODE." }

    & $uv.Source --cache-dir $uvCacheDir pip check --python $python
    if ($LASTEXITCODE -ne 0) { throw "uv package compatibility check failed." }

    & $python $smokeCheck --json
    if ($LASTEXITCODE -ne 0) { throw "ML environment smoke check failed." }
} finally {
    Pop-Location
}

Write-Host "Clean ML environment is ready: $python"
