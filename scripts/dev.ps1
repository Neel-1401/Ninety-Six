# Ninety-Six — run the app locally on Windows (PowerShell 5.1+).
# Usage:  powershell -ExecutionPolicy Bypass -File .\scripts\dev.ps1            # backend :8000 + Vite :5173 (two windows)
#         powershell -ExecutionPolicy Bypass -File .\scripts\dev.ps1 -Single    # build the UI once, serve everything on :8000
# Run scripts\setup.ps1 first.
param([switch]$Single)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root "backend"
$frontend = Join-Path $root "frontend"
$py = Join-Path $backend ".venv\Scripts\python.exe"

if (-not (Test-Path $py)) { Write-Host "Run scripts\setup.ps1 first (backend\.venv missing)." -ForegroundColor Red; exit 1 }
if (-not (Test-Path (Join-Path $frontend "node_modules"))) { Write-Host "Run scripts\setup.ps1 first (frontend\node_modules missing)." -ForegroundColor Red; exit 1 }

foreach ($port in @(8000, 5173)) {
    if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
        Write-Host "Port $port is already in use. Stop whatever is using it and retry." -ForegroundColor Yellow
        exit 1
    }
}

if ($Single) {
    Write-Host "==> Building the frontend" -ForegroundColor Cyan
    Push-Location $frontend
    try { & npm run build; if ($LASTEXITCODE -ne 0) { throw "frontend build failed" } } finally { Pop-Location }
    Write-Host "==> Serving API + UI on http://localhost:8000" -ForegroundColor Green
    Push-Location $backend
    try { & $py -m uvicorn app.main:app --host 127.0.0.1 --port 8000 } finally { Pop-Location }
    exit 0
}

Write-Host "==> Starting backend on http://localhost:8000 (new window)" -ForegroundColor Cyan
Start-Process powershell -WorkingDirectory $backend -ArgumentList @(
    "-NoExit", "-Command", "& '$py' -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload"
)

Write-Host "==> Starting frontend on http://localhost:5173 (new window)" -ForegroundColor Cyan
Start-Process powershell -WorkingDirectory $frontend -ArgumentList @("-NoExit", "-Command", "npm run dev")

Write-Host ""
Write-Host "Open http://localhost:5173 . Close the two windows to stop." -ForegroundColor Green
Write-Host "Demo staff accounts: dr.mehta, dr.iyer, nurse.asha, nurse.ravi (password demo1234; demo mode only)."
