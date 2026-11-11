# Isolated demo journey. Messaging, calendar, email and the LLM are forced to simulation;
# openFDA (public, keyless, read-only) is queried live unless -Offline is given, with a local fallback.
param([int]$Port = 8010, [switch]$SkipBuild, [switch]$Offline, [string]$Database = "recovery_demo.db")
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root "backend"
$frontend = Join-Path $root "frontend"
$py = Join-Path $backend ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "Run scripts\setup.ps1 first." }
if ($Port -lt 1024 -or $Port -gt 65535) { throw "Port must be between 1024 and 65535." }
if (-not $SkipBuild) {
    Push-Location $frontend
    try {
        & npm.cmd run build
        if ($LASTEXITCODE -ne 0) { throw "Frontend build failed." }
    } finally { Pop-Location }
}
if (-not (Test-Path (Join-Path $frontend "dist\index.html"))) { throw "Build the frontend first." }
$env:APP_MODE = "demo"
if ($Database -notmatch '^[\w.-]+\.db$') { throw "Database must be a plain file name ending in .db" }
$env:DATABASE_URL = "sqlite:///$Database"
$env:LLM_PROVIDER = "mock"
$env:OPENFDA_ENABLED = $(if ($Offline) { "false" } else { "true" })
$env:ALLOW_LIVE_TO_REAL_RECIPIENTS = "false"
$env:NS_SEED_BACKGROUND = "false"
$env:WORKER_ENABLED = "true"
$env:PUBLIC_BASE_URL = "http://localhost:$Port"
$env:FRONTEND_ORIGIN = "http://localhost:$Port"
Write-Host "Demo: http://localhost:$Port (database backend\$Database; openFDA $(if ($Offline) {'offline'} else {'live'}); Ctrl+C to stop)"
Push-Location $backend
try {
    # Set empty values inside Python before app imports: PowerShell 5.1 removes
    # empty env values, which would otherwise let dotenv restore real keys.
    & $py -c "import os, sys; os.environ.update(dict.fromkeys(['TWILIO_ACCOUNT_SID','TWILIO_AUTH_TOKEN','TWILIO_FROM','GOOGLE_SERVICE_ACCOUNT_FILE','GOOGLE_CALENDAR_ID','SMTP_HOST','CARE_TEAM_EMAIL','DEMO_PHONE'], '')); import uvicorn; uvicorn.run('app.main:app', host='127.0.0.1', port=int(sys.argv[1]), access_log=False)" $Port
    if ($LASTEXITCODE -ne 0) { throw "Demo server stopped with exit code $LASTEXITCODE." }
} finally { Pop-Location }
