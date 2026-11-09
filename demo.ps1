# One command to try Loan-Saathi locally (Windows PowerShell):   .\demo.ps1
# Installs what's missing, builds the dashboard, starts the API on 127.0.0.1:8000 (never on the network)
# and opens the dashboard. Speech needs Gnani keys in .env; without them, type instead of talking.
# Exit codes are checked by hand: in Windows PowerShell 5.1 a program writing to stderr would otherwise
# stop the script even when it succeeded.
Set-Location $PSScriptRoot

function Fail($message) { Write-Host $message -ForegroundColor Red; exit 1 }

if (-not (Get-Command py -ErrorAction SilentlyContinue) -and -not (Get-Command python -ErrorAction SilentlyContinue)) {
    Fail "Python 3.10+ is required (https://www.python.org/downloads/)."
}
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { Fail "Node.js 20.19+ is required (https://nodejs.org)." }

if (-not (Test-Path .venv)) {
    Write-Host "Creating .venv..."
    if (Get-Command py -ErrorAction SilentlyContinue) { py -m venv .venv } else { python -m venv .venv }
    if ($LASTEXITCODE -ne 0) { Fail "Couldn't create .venv." }
}
$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
cmd /c "`"$py`" -c `"import fastapi, lightgbm, uvicorn`" >nul 2>nul"
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing Python packages (a few minutes the first time)..."
    cmd /c "`"$py`" -m pip install -q -r requirements.txt"
    if ($LASTEXITCODE -ne 0) { Fail "pip install failed." }
}

# A local .env with a fresh random WEBHOOK_KEY if there isn't one yet (never commit .env).
$newKey = $null
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
$envText = Get-Content .env -Raw
if ($envText -match "(?m)^WEBHOOK_KEY=(replace-with.*|\s*)$") {
    $newKey = -join ((48..57) + (97..122) | Get-Random -Count 32 | ForEach-Object { [char]$_ })
    $envText = $envText -replace "(?m)^WEBHOOK_KEY=.*$", "WEBHOOK_KEY=$newKey"
    [IO.File]::WriteAllText((Join-Path $PSScriptRoot ".env"), $envText)
}

$dist = "dashboard\dist\index.html"
$stale = -not (Test-Path $dist)
if (-not $stale) {
    $newest = (Get-ChildItem dashboard\src -Recurse -File | Measure-Object LastWriteTime -Maximum).Maximum
    $stale = $newest -gt (Get-Item $dist).LastWriteTime
}
if ($stale) {
    Write-Host "Building the dashboard..."
    Push-Location dashboard
    if (-not (Test-Path node_modules)) {
        cmd /c "npm ci --no-audit --no-fund"
        if ($LASTEXITCODE -ne 0) { Pop-Location; Fail "npm ci failed." }
    }
    cmd /c "npm run build"
    if ($LASTEXITCODE -ne 0) { Pop-Location; Fail "The dashboard build failed." }
    Pop-Location
}

Write-Host ""
Write-Host "Loan-Saathi is starting at http://127.0.0.1:8000/dashboard/"
if ($newKey) { Write-Host "Your new local key (also saved in .env as WEBHOOK_KEY): $newKey" }
else { Write-Host "Sign in with the WEBHOOK_KEY from your .env file." }
Write-Host "Press Ctrl+C to stop."
if (-not $env:SAATHI_NO_BROWSER) { Start-Process "http://127.0.0.1:8000/dashboard/" }
& $py -I -X utf8 src/server.py
