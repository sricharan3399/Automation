<#
.SYNOPSIS
    First-time setup for the AV Test Automation Platform on Windows.

.DESCRIPTION
    Invoked by SETUP_AND_START.bat. Validates the machine, creates the virtual
    environment, installs dependencies, builds the dashboard, initialises the
    database, then starts the application and opens the browser.

    Idempotent and incremental: work whose inputs have not changed is skipped,
    so re-running is cheap. State lives in .runtime/setup_state.json.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\bootstrap_windows.ps1

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\bootstrap_windows.ps1 -NoStart -Force
#>

[CmdletBinding()]
param(
    [switch]$NoStart,        # set up but do not launch
    [switch]$NoBrowser,      # launch, but do not open a browser (headless / CI)
    [switch]$Force,          # reinstall and rebuild even if inputs are unchanged
    [switch]$SkipDashboard,  # skip Node install and the frontend build
    [switch]$NoShortcut
)

. "$PSScriptRoot\common.ps1"

$ErrorActionPreference = 'Stop'
Set-Location (Get-RepoRoot)

$logFile = Initialize-Log -Name 'setup'
Write-Banner 'AV TEST AUTOMATION DASHBOARD - FIRST TIME SETUP'
Write-Host "Repository: $(Get-RepoRoot)"
Write-Host "Setup log:  $logFile"

$state = Get-SetupState

function Invoke-Step {
    <#
        Run a native command, stream it to the console and the log, and stop the
        whole bootstrap with an explicit failure report if it fails.

        Success is judged on the exit code alone. Tools like npm and git write
        warnings and progress to stderr on a completely successful run, so
        treating stderr output as failure would abort valid installs.
    #>
    param(
        [string]$Stage,
        [string]$Display,
        [scriptblock]$Action
    )
    Write-Log "RUN: $Display"
    $code = Invoke-Native -Action $Action -LogPath $logFile
    if ($code -ne 0) {
        Write-StageFailure -Stage $Stage -Command $Display -Detail "Exit code $code"
        exit 1
    }
}

# ---------------------------------------------------------------------------
# 1. Validate the machine
# ---------------------------------------------------------------------------
Write-Stage '1/9  Validating this workstation'
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'validate_environment.ps1')
if ($LASTEXITCODE -ne 0) {
    Write-StageFailure -Stage 'System validation' -Command 'scripts\validate_environment.ps1' `
        -Detail 'A blocking requirement is not satisfied. Resolve the FAIL entries above and re-run SETUP_AND_START.bat.'
    exit 1
}

# ---------------------------------------------------------------------------
# 2. Runtime directories
# ---------------------------------------------------------------------------
Write-Stage '2/9  Creating runtime directories'
$created = Initialize-RuntimeDirectories
if ($created -gt 0) { Write-Ok "Created $created directory(ies)" } else { Write-Skip 'All runtime directories already exist' }

# ---------------------------------------------------------------------------
# 3. Local configuration
# ---------------------------------------------------------------------------
Write-Stage '3/9  Preparing local configuration'
Initialize-EnvFile | Out-Null

# ---------------------------------------------------------------------------
# 4. Virtual environment
# ---------------------------------------------------------------------------
Write-Stage '4/9  Python virtual environment'
$venvPython = Get-VenvPython
$venvCreated = $false
if ((Test-Path $venvPython) -and -not $Force) {
    Write-Skip ".venv already exists"
} else {
    $venvCreated = $true
    $interpreter = $null
    foreach ($candidate in @('python', 'py')) {
        $found = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($found) {
            $raw = & $candidate --version
            if ($raw -match 'Python 3\.(\d+)' -and [int]$Matches[1] -ge 10) { $interpreter = $candidate; break }
        }
    }
    if (-not $interpreter) {
        Write-StageFailure -Stage 'Virtual environment' -Command 'python -m venv .venv' `
            -Detail 'Python is required but could not be found. Please install an approved Python 3.10+ and rerun SETUP_AND_START.bat.'
        exit 1
    }
    Invoke-Step -Stage 'Virtual environment' -Display "$interpreter -m venv .venv" -Action { & $interpreter -m venv .venv }
    if (-not (Test-Path $venvPython)) {
        Write-StageFailure -Stage 'Virtual environment' -Command 'python -m venv .venv' -Detail "$venvPython was not created."
        exit 1
    }
    Write-Ok 'Created .venv'
}

# ---------------------------------------------------------------------------
# 5. Python dependencies
# ---------------------------------------------------------------------------
Write-Stage '5/9  Python dependencies'
$pythonHash = Get-PythonDependencyHash
$storedPythonHash = ''
if ($state.hashes.ContainsKey('python')) { $storedPythonHash = $state.hashes['python'] }

# Corroborate the recorded flag against what is actually installed.
#
# Trusting state + hash alone bricked the documented repair path: deleting .venv
# changes no dependency file, so the hash still matched and setup created an
# EMPTY venv then skipped pip entirely - failing later at init-db and blaming the
# database for a missing-dependency fault. Every re-run reproduced it.
$depsImportable = $false
if (-not $venvCreated -and (Test-Path $venvPython)) {
    $probe = Invoke-Native -Action { & $venvPython -c "import fastapi, uvicorn, sqlalchemy, shapely, yaml, pydantic" } -Quiet
    $depsImportable = ($probe -eq 0)
}

if (-not $Force -and -not $venvCreated -and $depsImportable -and $state.backend_dependencies -and $pythonHash -and $pythonHash -eq $storedPythonHash) {
    Write-Skip 'Already current (dependency files unchanged, packages importable)'
} else {
    if ($venvCreated) {
        Write-Host '    New virtual environment; installing dependencies' -ForegroundColor Gray
    } elseif (-not $depsImportable) {
        Write-Host '    Packages missing or broken; reinstalling' -ForegroundColor Gray
    }
    Invoke-Step -Stage 'Python dependencies' -Display 'pip install --upgrade pip' `
        -Action { & $venvPython -m pip install --upgrade pip --disable-pip-version-check --quiet }

    $requirements = 'requirements-dev.txt'
    if (-not (Test-Path (Join-Path (Get-RepoRoot) $requirements))) { $requirements = 'requirements.txt' }
    Invoke-Step -Stage 'Python dependencies' -Display "pip install -r $requirements" `
        -Action { & $venvPython -m pip install -r $requirements --disable-pip-version-check }

    $state.backend_dependencies = $true
    $state.hashes['python'] = $pythonHash
    Save-SetupState $state
    Write-Ok 'Python dependencies installed'
}

# ---------------------------------------------------------------------------
# 6. Frontend dependencies
# ---------------------------------------------------------------------------
Write-Stage '6/9  Dashboard dependencies'
$dashboardDir = Get-DashboardDir
$npm = Get-Command npm -ErrorAction SilentlyContinue
$dashboardPossible = (Test-Path (Join-Path $dashboardDir 'package.json')) -and $npm -and -not $SkipDashboard

if (-not $dashboardPossible) {
    if ($SkipDashboard) {
        Write-Skip 'Skipped by -SkipDashboard'
    } elseif (-not $npm) {
        Write-WarnMsg 'npm was not found. Install Node.js 18+ to build the dashboard.'
        Write-WarnMsg 'The backend API remains fully usable at /api/docs.'
    }
} else {
    $nodeHash = Get-NodeDependencyHash
    $storedNodeHash = ''
    if ($state.hashes.ContainsKey('node')) { $storedNodeHash = $state.hashes['node'] }
    $modulesPresent = Test-Path (Join-Path $dashboardDir 'node_modules')

    if (-not $Force -and $state.frontend_dependencies -and $modulesPresent -and $nodeHash -eq $storedNodeHash) {
        Write-Skip 'Already current (lockfile unchanged)'
    } else {
        Push-Location $dashboardDir
        try {
            # `npm ci` is reproducible and honours the lockfile exactly.
            if (Test-Path 'package-lock.json') {
                Invoke-Step -Stage 'Dashboard dependencies' -Display 'npm ci' -Action { npm ci --no-audit --no-fund }
            } else {
                Invoke-Step -Stage 'Dashboard dependencies' -Display 'npm install' -Action { npm install --no-audit --no-fund }
            }
        } finally {
            Pop-Location
        }
        $state.frontend_dependencies = $true
        $state.hashes['node'] = $nodeHash
        Save-SetupState $state
        Write-Ok 'Dashboard dependencies installed'
    }
}

# ---------------------------------------------------------------------------
# 7. Frontend build
# ---------------------------------------------------------------------------
Write-Stage '7/9  Dashboard build'
$distIndex = Join-Path $dashboardDir 'dist\index.html'

if (-not $dashboardPossible) {
    if (Test-Path $distIndex) {
        Write-Skip 'Using the existing dashboard build'
    } else {
        Write-WarnMsg 'The dashboard is not built. The API works; the UI will not be served.'
    }
} else {
    $sourceHash = Get-FrontendSourceHash
    $storedSourceHash = ''
    if ($state.hashes.ContainsKey('frontend_src')) { $storedSourceHash = $state.hashes['frontend_src'] }

    if (-not $Force -and $state.frontend_built -and (Test-Path $distIndex) -and $sourceHash -eq $storedSourceHash) {
        Write-Skip 'Already current (dashboard sources unchanged)'
    } else {
        Push-Location $dashboardDir
        try {
            Invoke-Step -Stage 'Frontend build' -Display 'npm run build' -Action { npm run build }
        } finally {
            Pop-Location
        }
        if (-not (Test-Path $distIndex)) {
            Write-StageFailure -Stage 'Frontend build' -Command 'npm run build' -Detail "$distIndex was not produced."
            exit 1
        }
        $state.frontend_built = $true
        $state.hashes['frontend_src'] = $sourceHash
        Save-SetupState $state
        Write-Ok 'Dashboard built'
    }
}

# ---------------------------------------------------------------------------
# 8. Database
# ---------------------------------------------------------------------------
Write-Stage '8/9  Local database'
$databaseFile = Join-Path (Get-RepoRoot) 'data\local.db'
if (Test-Path $databaseFile) {
    # Back up before touching an existing database, even though init-db is
    # additive. A backup that was never needed costs nothing.
    $stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $backup = Join-Path (Get-BackupDir) "database_$stamp.db"
    Copy-Item $databaseFile $backup -ErrorAction SilentlyContinue
    Write-Ok "Backed up the existing database to $(Split-Path -Leaf $backup)"
}

Invoke-Step -Stage 'Database initialisation' -Display 'python -m backend.cli init-db' `
    -Action { & $venvPython -m backend.cli init-db }
$state.database_initialized = $true
Save-SetupState $state
Write-Ok 'Database schema ready and built-in profiles seeded'

# Offline fixtures, so the platform is usable before a source is connected.
$goldenEvents = Join-Path (Get-RepoRoot) 'tests\golden_dataset\events'
if (-not (Test-Path $goldenEvents)) {
    Invoke-Step -Stage 'Golden dataset' -Display 'python tests/golden_dataset/generate.py' `
        -Action { & $venvPython 'tests\golden_dataset\generate.py' }
    Write-Ok 'Generated the synthetic golden dataset'
} else {
    Write-Skip 'Golden dataset already present'
}

# ---------------------------------------------------------------------------
# 9. Shortcut and launch
# ---------------------------------------------------------------------------
Write-Stage '9/9  Finishing setup'
$state.setup_completed = $true
Save-SetupState $state
Write-Ok "Setup state saved to $(Get-StateFile)"

if (-not $NoShortcut) {
    try {
        $shell = New-Object -ComObject WScript.Shell
        $desktop = [Environment]::GetFolderPath('Desktop')
        $shortcutPath = Join-Path $desktop 'AV Test Automation Dashboard.lnk'
        $shortcut = $shell.CreateShortcut($shortcutPath)
        $shortcut.TargetPath = Join-Path (Get-RepoRoot) 'START_AV_DASHBOARD.bat'
        $shortcut.WorkingDirectory = (Get-RepoRoot)
        $shortcut.Description = 'AV Test Automation Dashboard'
        $shortcut.Save()
        Write-Ok "Desktop shortcut created: 'AV Test Automation Dashboard'"
    } catch {
        Write-WarnMsg "Could not create the desktop shortcut: $($_.Exception.Message)"
    }
}

if ($NoStart) {
    Write-Banner 'SETUP COMPLETE'
    Write-Host ''
    Write-Host '  Start the dashboard with: START_AV_DASHBOARD.bat' -ForegroundColor Gray
    Write-Host ''
    exit 0
}

Write-Banner 'STARTING THE DASHBOARD'
$startScript = Join-Path $PSScriptRoot 'start_dashboard.ps1'
if ($NoBrowser) {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $startScript -NoBrowser
} else {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $startScript
}
exit $LASTEXITCODE
