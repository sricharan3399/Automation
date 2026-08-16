<#
.SYNOPSIS
    One-click installer for the AV Test Automation Platform on Windows.

.DESCRIPTION
    Validates the environment, creates a virtual environment, installs
    dependencies, initialises the local database, builds the dashboard and
    creates a Start Menu / Desktop shortcut.

    It never contacts a data source and never starts a scout query.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File install_windows.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File install_windows.ps1 -SkipDashboard -NoShortcut
#>

[CmdletBinding()]
param(
    [switch]$SkipDashboard,
    [switch]$SkipTests,
    [switch]$NoShortcut,
    [switch]$Start
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$MinPythonMajor = 3
$MinPythonMinor = 10

function Write-Step($message) { Write-Host "`n==> $message" -ForegroundColor Cyan }
function Write-Ok($message)   { Write-Host "    [ OK ]   $message" -ForegroundColor Green }
function Write-Warn($message) { Write-Host "    [ WARN ] $message" -ForegroundColor Yellow }
function Write-Fail($message) { Write-Host "    [ FAIL ] $message" -ForegroundColor Red }

Write-Host "=================================================" -ForegroundColor White
Write-Host " AV TEST AUTOMATION PLATFORM - WINDOWS INSTALLER"  -ForegroundColor White
Write-Host "=================================================" -ForegroundColor White

# ---------------------------------------------------------------------------
# 1. Validate Python
# ---------------------------------------------------------------------------
Write-Step "1/9  Validating Python"

$pythonExe = $null
foreach ($candidate in @('python', 'py')) {
    try {
        $version = & $candidate --version 2>&1
        if ($version -match 'Python (\d+)\.(\d+)\.(\d+)') {
            $major = [int]$Matches[1]; $minor = [int]$Matches[2]
            if ($major -gt $MinPythonMajor -or ($major -eq $MinPythonMajor -and $minor -ge $MinPythonMinor)) {
                $pythonExe = $candidate
                Write-Ok "$version found via '$candidate'"
                if ($minor -lt 11) { Write-Warn "Python 3.11+ is recommended; 3.$minor is supported." }
                break
            }
        }
    } catch { }
}

if (-not $pythonExe) {
    Write-Fail "Python $MinPythonMajor.$MinPythonMinor or newer was not found on PATH."
    Write-Host "    Install it from https://www.python.org/downloads/ and re-run this script."
    exit 1
}

# ---------------------------------------------------------------------------
# 2. Virtual environment
# ---------------------------------------------------------------------------
Write-Step "2/9  Creating the virtual environment"
$venvPython = Join-Path $Root '.venv\Scripts\python.exe'
if (Test-Path $venvPython) {
    Write-Ok ".venv already exists - reusing it"
} else {
    & $pythonExe -m venv .venv
    if (-not (Test-Path $venvPython)) { Write-Fail "Could not create .venv"; exit 1 }
    Write-Ok "Created .venv"
}

# ---------------------------------------------------------------------------
# 3. Dependencies
# ---------------------------------------------------------------------------
Write-Step "3/9  Installing Python dependencies"
& $venvPython -m pip install --upgrade pip --quiet
if ($SkipTests) {
    & $venvPython -m pip install -r requirements.txt
} else {
    & $venvPython -m pip install -r requirements-dev.txt
}
if ($LASTEXITCODE -ne 0) { Write-Fail "Dependency installation failed"; exit 1 }
Write-Ok "Dependencies installed"

# ---------------------------------------------------------------------------
# 4. Directories and local configuration
# ---------------------------------------------------------------------------
Write-Step "4/9  Preparing directories"
foreach ($dir in @('data', 'data\checkpoints', 'data\cache', 'output')) {
    $path = Join-Path $Root $dir
    if (-not (Test-Path $path)) { New-Item -ItemType Directory -Path $path -Force | Out-Null }
}
Write-Ok "data/ and output/ ready"

if (-not (Test-Path (Join-Path $Root '.env'))) {
    Copy-Item (Join-Path $Root '.env.example') (Join-Path $Root '.env')
    Write-Ok "Created .env from .env.example (gitignored - never commit it)"
} else {
    Write-Ok ".env already exists - left untouched"
}

# ---------------------------------------------------------------------------
# 5. Environment checks
# ---------------------------------------------------------------------------
Write-Step "5/9  Running environment checks"
& $venvPython -m backend.cli check
if ($LASTEXITCODE -ne 0) { Write-Warn "Some environment checks did not pass - see above." }

# ---------------------------------------------------------------------------
# 6. Database
# ---------------------------------------------------------------------------
Write-Step "6/9  Initialising the local database"
& $venvPython -m backend.cli init-db | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Fail "Database initialisation failed"; exit 1 }
Write-Ok "SQLite schema created and built-in profiles seeded"

# ---------------------------------------------------------------------------
# 7. Golden dataset
# ---------------------------------------------------------------------------
Write-Step "7/9  Preparing the golden dataset"
if (-not (Test-Path (Join-Path $Root 'tests\golden_dataset\events'))) {
    & $venvPython tests\golden_dataset\generate.py
}
Write-Ok "Synthetic golden dataset available for offline testing"

# ---------------------------------------------------------------------------
# 8. Dashboard
# ---------------------------------------------------------------------------
Write-Step "8/9  Building the dashboard"
if ($SkipDashboard) {
    Write-Warn "Skipped (-SkipDashboard). The API works; the UI will not be served."
} else {
    $npm = Get-Command npm -ErrorAction SilentlyContinue
    if (-not $npm) {
        Write-Warn "npm was not found. Install Node.js 18+ and re-run, or use -SkipDashboard."
        Write-Warn "The backend API remains fully usable at /api/docs."
    } else {
        Push-Location (Join-Path $Root 'dashboard')
        try {
            npm install --no-audit --no-fund
            npm run build
            if ($LASTEXITCODE -ne 0) { throw "dashboard build failed" }
            Write-Ok "Dashboard built to dashboard\dist"
        } catch {
            Write-Warn "Dashboard build failed: $_"
            Write-Warn "The backend API remains usable; re-run 'npm run build' in dashboard\ to retry."
        } finally {
            Pop-Location
        }
    }
}

# ---------------------------------------------------------------------------
# 9. Shortcut
# ---------------------------------------------------------------------------
Write-Step "9/9  Creating the launch shortcut"
if ($NoShortcut) {
    Write-Warn "Skipped (-NoShortcut)"
} else {
    try {
        $shell = New-Object -ComObject WScript.Shell
        $desktop = [Environment]::GetFolderPath('Desktop')
        $shortcut = $shell.CreateShortcut((Join-Path $desktop 'Start AV Scout Dashboard.lnk'))
        $shortcut.TargetPath = $venvPython
        $shortcut.Arguments = '"' + (Join-Path $Root 'launcher.py') + '" start'
        $shortcut.WorkingDirectory = $Root
        $shortcut.Description = 'AV Test Automation Platform'
        $shortcut.Save()
        Write-Ok "Desktop shortcut: 'Start AV Scout Dashboard'"
    } catch {
        Write-Warn "Could not create the shortcut: $_"
    }
}

Write-Host "`n=================================================" -ForegroundColor White
Write-Host " INSTALLATION COMPLETE" -ForegroundColor Green
Write-Host "=================================================" -ForegroundColor White
Write-Host @"

Start the platform:

    .\.venv\Scripts\python.exe launcher.py

Dashboard:   http://localhost:8000
API docs:    http://localhost:8000/api/docs

Default posture:
    Operating mode          production
    Source access           READ ONLY
    Production submission   DISABLED
    Data Scout adapter      NOT CONFIGURED (supply approved details in Connections)

No data-source query is started automatically.
"@ -ForegroundColor Gray

if ($Start) {
    Write-Step "Starting the platform"
    & $venvPython (Join-Path $Root 'launcher.py') start
}
