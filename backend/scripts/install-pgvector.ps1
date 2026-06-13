# Install pgvector for the local PostgreSQL and enable it on the Inspiration DB.
# Windows has no official pgvector binary -> it is built from source with MSVC.
#
# PREREQUISITE (one-time): "Visual Studio Build Tools" with the
#   "Desktop development with C++" workload. Free download:
#   https://visualstudio.microsoft.com/visual-cpp-build-tools/
#
# Usage (it self-elevates to Administrator, needed to write into Program Files):
#   ./scripts/install-pgvector.ps1
#   ./scripts/install-pgvector.ps1 -PgRoot "C:\Program Files\PostgreSQL\18" -Db inspiration -Ref v0.8.0
#
# After it finishes, set ENABLE_PGVECTOR=true in backend\.env and restart the backend.
#requires -Version 5.1
param(
  [string]$PgRoot = "C:\Program Files\PostgreSQL\18",
  [string]$Db     = "inspiration",
  [string]$PgUser = "postgres",
  [string]$Ref    = "master"   # pgvector ref; PG 18 needs a recent version. Pin a tag if you prefer.
)
$ErrorActionPreference = "Stop"

# --- self-elevate ---
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
  Write-Host "Elevating to Administrator (required to install into Program Files)..." -ForegroundColor Yellow
  $argList = "-NoExit -NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -PgRoot `"$PgRoot`" -Db `"$Db`" -PgUser `"$PgUser`" -Ref `"$Ref`""
  Start-Process powershell.exe -Verb RunAs -ArgumentList $argList
  return
}

Write-Host "== pgvector installer ==" -ForegroundColor Cyan
Write-Host "PgRoot=$PgRoot  Db=$Db  Ref=$Ref"

# --- validate PostgreSQL dev files ---
if (-not (Test-Path "$PgRoot\include\server\postgres.h")) {
  Write-Host "PostgreSQL dev headers not found under $PgRoot\include\server. Wrong PgRoot?" -ForegroundColor Red
  exit 1
}

# --- locate Visual C++ build tools (vcvars64.bat) ---
$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
$vcvars = $null
if (Test-Path $vswhere) {
  $vsPath = & $vswhere -latest -products * -requires Microsoft.VisualCpp.Tools.Host.x64 -property installationPath 2>$null
  if ($vsPath) { $cand = Join-Path $vsPath "VC\Auxiliary\Build\vcvars64.bat"; if (Test-Path $cand) { $vcvars = $cand } }
}
if (-not $vcvars) {
  Write-Host "MSVC C++ build tools not found." -ForegroundColor Red
  Write-Host "Install 'Visual Studio Build Tools' with the 'Desktop development with C++' workload, then re-run:" -ForegroundColor Yellow
  Write-Host "  https://visualstudio.microsoft.com/visual-cpp-build-tools/"
  exit 1
}
Write-Host "Using vcvars: $vcvars" -ForegroundColor DarkGray

# --- build + install via a temp batch that loads the MSVC environment ---
$work = Join-Path $env:TEMP ("pgvector_build_" + [System.Guid]::NewGuid().ToString("N").Substring(0,8))
New-Item -ItemType Directory -Path $work -Force | Out-Null
$bat = Join-Path $work "build.bat"
@"
@echo on
call "$vcvars" || exit /b 1
set "PGROOT=$PgRoot"
cd /d "$work" || exit /b 1
git clone --depth 1 --branch $Ref https://github.com/pgvector/pgvector.git || git clone --depth 1 https://github.com/pgvector/pgvector.git || exit /b 1
cd pgvector || exit /b 1
nmake /F Makefile.win || exit /b 1
nmake /F Makefile.win install || exit /b 1
"@ | Set-Content -Path $bat -Encoding ascii

Write-Host "Building pgvector (clone + nmake)..." -ForegroundColor Green
& cmd.exe /c "`"$bat`""
if ($LASTEXITCODE -ne 0) {
  Write-Host "Build/install failed (exit $LASTEXITCODE). See output above." -ForegroundColor Red
  Write-Host "If it's a PG-version error, pin a newer pgvector: -Ref v0.8.0 (or latest tag supporting PG 18)." -ForegroundColor Yellow
  exit 1
}
Write-Host "pgvector binary installed into $PgRoot\lib and $PgRoot\share\extension." -ForegroundColor Green

# --- enable on the database (CREATE EXTENSION + add embedding columns) ---
$psql = Join-Path $PgRoot "bin\psql.exe"
$sql = Join-Path $PSScriptRoot "enable-pgvector.sql"
if ((Test-Path $psql) -and (Test-Path $sql)) {
  Write-Host "Enabling on database '$Db' (you'll be prompted for the '$PgUser' password)..." -ForegroundColor Green
  & $psql -U $PgUser -d $Db -v ON_ERROR_STOP=1 -f $sql
  if ($LASTEXITCODE -ne 0) {
    Write-Host "psql step failed; run it manually:" -ForegroundColor Yellow
    Write-Host "  `"$psql`" -U $PgUser -d $Db -f `"$sql`""
  } else {
    Write-Host "Extension + embedding columns enabled." -ForegroundColor Green
  }
} else {
  Write-Host "psql or enable-pgvector.sql not found; run scripts/enable-pgvector.sql manually." -ForegroundColor Yellow
}

Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
Write-Host ""
Write-Host "DONE. Final step: set ENABLE_PGVECTOR=true in backend\.env and restart the backend (run.ps1)." -ForegroundColor Cyan
