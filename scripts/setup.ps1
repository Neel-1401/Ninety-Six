# Ninety-Six — one-time setup on Windows (PowerShell 5.1+).
# Usage (from the repo root):   powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1
# Creates backend\.venv, installs Python and Node dependencies, creates backend\.env from the
# example (simulated mode, no keys needed) and migrates the local SQLite database.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root "backend"
$frontend = Join-Path $root "frontend"

function Need($cmd, $hint) {
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
        Write-Host "Missing '$cmd'. $hint" -ForegroundColor Red
        exit 1
    }
}

Need "python" "Install Python 3.11+ from https://www.python.org/downloads/ and tick 'Add to PATH'."
Need "node" "Install Node.js 20+ from https://nodejs.org/."
Need "npm" "Install Node.js 20+ (includes npm)."

$pyVer = (& python -c "import sys; print('%d.%d' % sys.version_info[:2])")
if ([version]$pyVer -lt [version]"3.11") { Write-Host "Python $pyVer found; 3.11+ required." -ForegroundColor Red; exit 1 }
$nodeMajor = [int]((& node -p "process.versions.node").Split(".")[0])
if ($nodeMajor -lt 20) { Write-Host "Node $nodeMajor found; 20+ required." -ForegroundColor Red; exit 1 }

Write-Host "==> Backend: virtual environment and packages" -ForegroundColor Cyan
Push-Location $backend
try {
    if (-not (Test-Path ".venv\Scripts\python.exe")) { & python -m venv .venv }
    & .\.venv\Scripts\python.exe -m pip install --quiet --upgrade pip
    & .\.venv\Scripts\python.exe -m pip install --quiet -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw "pip install failed" }
    if (-not (Test-Path ".env")) {
        Copy-Item ".env.example" ".env"
        Write-Host "    Created backend\.env (simulated mode, no API keys)."
    } else {
        Write-Host "    backend\.env already exists; left unchanged."
    }
    if (Test-Path "alembic.ini") {
        Write-Host "==> Backend: database migrations" -ForegroundColor Cyan
        & .\.venv\Scripts\python.exe -m alembic upgrade head
        if ($LASTEXITCODE -ne 0) { throw "alembic upgrade failed" }
    }
} finally { Pop-Location }

Write-Host "==> Frontend: packages" -ForegroundColor Cyan
Push-Location $frontend
try {
    & npm ci --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { throw "npm ci failed" }
} finally { Pop-Location }

Write-Host ""
Write-Host "Setup complete. Start the app with:  powershell -ExecutionPolicy Bypass -File .\scripts\dev.ps1" -ForegroundColor Green
Write-Host "Then open http://localhost:5173  (demo staff: dr.mehta / nurse.asha, password demo1234)"
