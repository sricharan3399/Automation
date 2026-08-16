<#
.SYNOPSIS
    Update the AV Test Automation Dashboard from GitHub and restart it.

.DESCRIPTION
    Pulls the newest approved version, reinstalls or rebuilds only what actually
    changed, then starts the application.

    Local work is protected: if tracked source files are modified the update
    STOPS. It never runs `git reset --hard` or `git clean` on a tester's behalf.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\update_and_start.ps1
#>

[CmdletBinding()]
param([switch]$NoStart)

. "$PSScriptRoot\common.ps1"

$ErrorActionPreference = 'Stop'
Set-Location (Get-RepoRoot)

$logFile = Initialize-Log -Name 'update'
Write-Banner 'UPDATING THE AV TEST AUTOMATION DASHBOARD'

$venvPython = Get-VenvPython
$state = Get-SetupState

# ---------------------------------------------------------------------------
# 1. Git availability and repository
# ---------------------------------------------------------------------------
Write-Stage '1/8  Checking the repository'
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-StageFailure -Stage 'Update' -Command 'git' -Detail 'Git is not installed, so this installation cannot self-update.'
    exit 1
}
git rev-parse --is-inside-work-tree | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-StageFailure -Stage 'Update' -Command 'git rev-parse --is-inside-work-tree' `
        -Detail 'This folder is not a git repository. Re-clone it from GitHub to enable updates.'
    exit 1
}
$branch = (git rev-parse --abbrev-ref HEAD).Trim()
Write-Ok "On branch '$branch'"

# ---------------------------------------------------------------------------
# 2. Protect local modifications
# ---------------------------------------------------------------------------
Write-Stage '2/8  Checking for local modifications'
$dirty = @(git status --porcelain --untracked-files=no)
if ($dirty.Count -gt 0) {
    Write-Host ''
    Write-Host ('=' * 64) -ForegroundColor Yellow
    Write-Host ' UPDATE STOPPED' -ForegroundColor Yellow
    Write-Host ('=' * 64) -ForegroundColor Yellow
    Write-Host ''
    Write-Host '  Local source modifications detected.' -ForegroundColor Yellow
    Write-Host ''
    Write-Host '  Update stopped to prevent loss of work.' -ForegroundColor Yellow
    Write-Host ''
    Write-Host '  Modified files:' -ForegroundColor Gray
    foreach ($line in $dirty) { Write-Host "    $line" -ForegroundColor Gray }
    Write-Host ''
    Write-Host '  Commit, stash, or revert those changes manually before updating.' -ForegroundColor Yellow
    Write-Host ''
    Write-Host '    git stash push -m "before update"     # set aside' -ForegroundColor Gray
    Write-Host '    git commit -am "local changes"        # keep' -ForegroundColor Gray
    Write-Host ''
    exit 1
}
Write-Ok 'Working tree is clean'

# ---------------------------------------------------------------------------
# 3. Fetch
# ---------------------------------------------------------------------------
Write-Stage '3/8  Fetching from origin'
git fetch origin 2>&1 | Tee-Object -FilePath $logFile -Append | Out-Host
if ($LASTEXITCODE -ne 0) {
    Write-StageFailure -Stage 'Update' -Command 'git fetch origin' `
        -Detail 'Could not reach the remote. Check the network, the VPN, and your Git credentials.'
    exit 1
}

$localCommit = (git rev-parse HEAD).Trim()
$remoteRef = "origin/$branch"
git rev-parse --verify $remoteRef 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-WarnMsg "Branch '$branch' does not exist on origin. Nothing to update."
    if (-not $NoStart) { & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'start_dashboard.ps1') }
    exit $LASTEXITCODE
}
$remoteCommit = (git rev-parse $remoteRef).Trim()

if ($localCommit -eq $remoteCommit) {
    Write-Ok "Already up to date ($($localCommit.Substring(0,8)))"
    $pulled = $false
} else {
    Write-Host "    local  $($localCommit.Substring(0,8))" -ForegroundColor Gray
    Write-Host "    remote $($remoteCommit.Substring(0,8))" -ForegroundColor Gray

    Write-Stage '4/8  Pulling changes'
    # --ff-only: never create a surprise merge commit on a tester's machine.
    git pull --ff-only origin $branch 2>&1 | Tee-Object -FilePath $logFile -Append | Out-Host
    if ($LASTEXITCODE -ne 0) {
        Write-StageFailure -Stage 'Update' -Command "git pull --ff-only origin $branch" `
            -Detail 'The local branch has diverged from origin and cannot fast-forward. Resolve it manually; this script will not rewrite your history.'
        exit 1
    }
    Write-Ok "Updated to $((git rev-parse HEAD).Trim().Substring(0,8))"
    $pulled = $true
}

# ---------------------------------------------------------------------------
# 5. Stop the running instance before changing anything underneath it
# ---------------------------------------------------------------------------
Write-Stage '5/8  Stopping the running instance'
$running = Get-TrackedProcess -PidFile (Get-BackendPidFile)
if ($running) {
    Stop-TrackedProcess -PidFile (Get-BackendPidFile) -Label 'backend' | Out-Null
    Write-Ok 'Stopped'
} else {
    Write-Skip 'Not running'
}

# ---------------------------------------------------------------------------
# 6. Reinstall only what changed
# ---------------------------------------------------------------------------
Write-Stage '6/8  Applying dependency changes'

$pythonHash = Get-PythonDependencyHash
$storedPythonHash = ''
if ($state.hashes.ContainsKey('python')) { $storedPythonHash = $state.hashes['python'] }
if ($pythonHash -ne $storedPythonHash) {
    Write-Host '    Python dependencies changed; reinstalling' -ForegroundColor Gray
    $requirements = 'requirements-dev.txt'
    if (-not (Test-Path (Join-Path (Get-RepoRoot) $requirements))) { $requirements = 'requirements.txt' }
    & $venvPython -m pip install -r $requirements --disable-pip-version-check 2>&1 | Tee-Object -FilePath $logFile -Append | Out-Host
    if ($LASTEXITCODE -ne 0) {
        Write-StageFailure -Stage 'Python dependencies' -Command "pip install -r $requirements" -Detail "Exit code $LASTEXITCODE"
        exit 1
    }
    $state.hashes['python'] = $pythonHash
    Save-SetupState $state
    Write-Ok 'Python dependencies updated'
} else {
    Write-Skip 'Python dependencies unchanged'
}

$dashboardDir = Get-DashboardDir
$npm = Get-Command npm -ErrorAction SilentlyContinue
if ($npm -and (Test-Path (Join-Path $dashboardDir 'package.json'))) {
    $nodeHash = Get-NodeDependencyHash
    $storedNodeHash = ''
    if ($state.hashes.ContainsKey('node')) { $storedNodeHash = $state.hashes['node'] }
    if ($nodeHash -ne $storedNodeHash) {
        Write-Host '    Dashboard lockfile changed; reinstalling' -ForegroundColor Gray
        Push-Location $dashboardDir
        try {
            if (Test-Path 'package-lock.json') { npm ci --no-audit --no-fund } else { npm install --no-audit --no-fund }
        } finally { Pop-Location }
        if ($LASTEXITCODE -ne 0) {
            Write-StageFailure -Stage 'Dashboard dependencies' -Command 'npm ci' -Detail "Exit code $LASTEXITCODE"
            exit 1
        }
        $state.hashes['node'] = $nodeHash
        Save-SetupState $state
        Write-Ok 'Dashboard dependencies updated'
    } else {
        Write-Skip 'Dashboard dependencies unchanged'
    }
} else {
    Write-Skip 'npm unavailable; dashboard dependencies left untouched'
}

# ---------------------------------------------------------------------------
# 7. Database and frontend build
# ---------------------------------------------------------------------------
Write-Stage '7/8  Database and dashboard build'

$databaseFile = Join-Path (Get-RepoRoot) 'data\local.db'
if (Test-Path $databaseFile) {
    $stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $backup = Join-Path (Get-BackupDir) "database_$stamp.db"
    Copy-Item $databaseFile $backup -ErrorAction SilentlyContinue
    Write-Ok "Database backed up to $(Split-Path -Leaf $backup)"
}
# init-db is idempotent: it creates missing tables and seeds absent built-ins,
# and never overwrites existing rows.
& $venvPython -m backend.cli init-db 2>&1 | Tee-Object -FilePath $logFile -Append | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-StageFailure -Stage 'Database migration' -Command 'python -m backend.cli init-db' -Detail "Exit code $LASTEXITCODE"
    exit 1
}
Write-Ok 'Database schema is current'

if ($npm -and (Test-Path (Join-Path $dashboardDir 'package.json'))) {
    $sourceHash = Get-FrontendSourceHash
    $storedSourceHash = ''
    if ($state.hashes.ContainsKey('frontend_src')) { $storedSourceHash = $state.hashes['frontend_src'] }
    $distIndex = Join-Path $dashboardDir 'dist\index.html'

    if ($sourceHash -ne $storedSourceHash -or -not (Test-Path $distIndex)) {
        Write-Host '    Dashboard sources changed; rebuilding' -ForegroundColor Gray
        Push-Location $dashboardDir
        try { npm run build } finally { Pop-Location }
        if ($LASTEXITCODE -ne 0) {
            Write-StageFailure -Stage 'Frontend build' -Command 'npm run build' -Detail "Exit code $LASTEXITCODE"
            exit 1
        }
        $state.hashes['frontend_src'] = $sourceHash
        Save-SetupState $state
        Write-Ok 'Dashboard rebuilt'
    } else {
        Write-Skip 'Dashboard build is current'
    }
}

# ---------------------------------------------------------------------------
# 8. Smoke test and start
# ---------------------------------------------------------------------------
Write-Stage '8/8  Smoke test'
& $venvPython -c "from backend.main import create_app; create_app(); print('application imports cleanly')" 2>&1 |
    Tee-Object -FilePath $logFile -Append | Out-Host
if ($LASTEXITCODE -ne 0) {
    Write-StageFailure -Stage 'Smoke test' -Command 'import backend.main' `
        -Detail 'The updated application does not import cleanly. It was not started.'
    exit 1
}
Write-Ok 'Application imports cleanly'

if ($pulled) { Write-Ok 'Update applied' } else { Write-Ok 'No changes were pulled' }

if ($NoStart) { exit 0 }
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'start_dashboard.ps1')
exit $LASTEXITCODE
