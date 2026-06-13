# Inspiration backend launcher (local dev)
# Usage:  ./run.ps1            (defaults to 127.0.0.1:8000)
#         ./run.ps1 0.0.0.0 8000
# Auto: cd to this dir -> use venv Python -> load .env -> hot reload (--reload)
#requires -Version 5.1
$ErrorActionPreference = "Stop"

# cd to script dir (backend) so .env and the app package resolve correctly
Set-Location -Path $PSScriptRoot

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Host "venv Python not found: $python" -ForegroundColor Red
    Write-Host "Create it and install deps first:" -ForegroundColor Yellow
    Write-Host "  python -m venv .venv; .\.venv\Scripts\python.exe -m pip install -r requirements.txt"
    exit 1
}

if (-not (Test-Path (Join-Path $PSScriptRoot ".env"))) {
    Write-Host "Warning: no .env found; falling back to config.py defaults (may fail to reach the local DB)." -ForegroundColor Yellow
}

$bind = if ($args.Count -ge 1) { $args[0] } else { "127.0.0.1" }
$port = if ($args.Count -ge 2) { $args[1] } else { "8000" }

Write-Host "Starting Inspiration backend at http://${bind}:${port}  (hot reload ON)" -ForegroundColor Green
Write-Host "API docs: http://${bind}:${port}/docs   Press Ctrl+C to stop" -ForegroundColor DarkGray

& $python -m uvicorn app.main:app --host $bind --port $port --reload
