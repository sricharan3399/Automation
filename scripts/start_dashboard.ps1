<#
.SYNOPSIS
    Start the AV Test Automation Dashboard and open it in the browser.

.DESCRIPTION
    The everyday entry point, invoked by START_AV_DASHBOARD.bat. Performs
    lightweight checks only — it never reinstalls dependencies or rebuilds the
    dashboard. If setup has not been completed it says so and stops rather than
    silently doing a first-time install.

    The browser is opened only after /health reports healthy, so a failed start
    is never presented as a success.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start_dashboard.ps1

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start_dashboard.ps1 -NoBrowser
#>

[CmdletBinding()]
param(
    [switch]$NoBrowser,
    [int]$TimeoutSeconds = 90
)

. "$PSScriptRoot\common.ps1"

$ErrorActionPreference = 'Stop'
Set-Location (Get-RepoRoot)

$logFile = Initialize-Log -Name 'start'
Write-Banner 'AV TEST AUTOMATION DASHBOARD'

$url = Get-AppUrl
$port = Get-AppPort
$venvPython = Get-VenvPython
$pidFile = Get-BackendPidFile

# ---------------------------------------------------------------------------
# Already running?
# ---------------------------------------------------------------------------
Write-Stage 'Checking for a running instance'
$existing = Get-TrackedProcess -PidFile $pidFile
if ($existing) {
    $health = Wait-ForHealth -Url $url -TimeoutSeconds 10
    if ($health.healthy) {
        Write-Ok "Already running and healthy (PID $($existing.Id))"
        if (-not $NoBrowser) { Open-Dashboard -Url $url }
        Write-Host ''
        Write-Host "  Dashboard: $url" -ForegroundColor Green
        Write-Host ''
        exit 0
    }
    Write-WarnMsg "A tracked process is running but not healthy; restarting it"
    Stop-TrackedProcess -PidFile $pidFile -Label 'backend' | Out-Null
} else {
    Write-Ok 'No instance is currently running'
}

# ---------------------------------------------------------------------------
# Preconditions
# ---------------------------------------------------------------------------
Write-Stage 'Verifying the installation'

if (-not (Test-Path $venvPython)) {
    Write-StageFailure -Stage 'Startup checks' -Command '.venv\Scripts\python.exe' `
        -Detail 'The Python virtual environment is missing. Run SETUP_AND_START.bat first.'
    exit 1
}
Write-Ok 'Virtual environment present'

$state = Get-SetupState
if (-not $state.setup_completed) {
    Write-StageFailure -Stage 'Startup checks' -Command 'setup state' `
        -Detail 'Setup has not completed on this machine. Run SETUP_AND_START.bat first.'
    exit 1
}

# Fail fast on a broken environment rather than during request handling.
# Invoke-Native, not `2>&1`: a failing import writes a traceback to stderr, which
# under $ErrorActionPreference='Stop' would abort with a raw PowerShell error
# instead of the actionable failure report below.
$importCode = Invoke-Native -Action { & $venvPython -c "import fastapi, uvicorn, sqlalchemy, shapely" } -LogPath $logFile -Quiet
if ($importCode -ne 0) {
    Write-StageFailure -Stage 'Startup checks' -Command 'import fastapi, uvicorn, sqlalchemy, shapely' `
        -Detail 'Python dependencies are missing or broken. Run SETUP_AND_START.bat to repair the environment.'
    exit 1
}
Write-Ok 'Python dependencies importable'

if (-not (Test-Path (Join-Path (Get-RepoRoot) '.env'))) {
    Initialize-EnvFile | Out-Null
} else {
    Write-Ok 'Local configuration present'
}

Initialize-RuntimeDirectories | Out-Null

$databaseFile = Join-Path (Get-RepoRoot) 'data\local.db'
if (-not (Test-Path $databaseFile)) {
    Write-WarnMsg 'No local database found; initialising it now'
    $initCode = Invoke-Native -Action { & $venvPython -m backend.cli init-db } -LogPath $logFile -Quiet
    if ($initCode -ne 0) {
        Write-StageFailure -Stage 'Database initialisation' -Command 'python -m backend.cli init-db' `
            -Detail 'The database could not be created.'
        exit 1
    }
}
Write-Ok 'Database present'

$distIndex = Join-Path (Get-DashboardDir) 'dist\index.html'
if (Test-Path $distIndex) {
    Write-Ok 'Dashboard build present'
} else {
    Write-WarnMsg 'The dashboard is not built. The API will start; the UI will not be served.'
    Write-WarnMsg 'Run SETUP_AND_START.bat, or `npm run build` in dashboard\, to build it.'
}

# ---------------------------------------------------------------------------
# Port
# ---------------------------------------------------------------------------
Write-Stage "Checking port $port"
if (-not (Test-PortFree -Port $port)) {
    Write-StageFailure -Stage 'Port check' -Command "bind 127.0.0.1:$port" `
        -Detail "Port $port is already in use by another process. Set AV_PORT in .env to use a different port, or stop the other process."
    exit 1
}
Write-Ok "Port $port is available"

# ---------------------------------------------------------------------------
# Start
# ---------------------------------------------------------------------------
Write-Stage 'Starting the backend'
$backendLog = Join-Path (Get-LogDir) 'backend.log'
$backendErr = Join-Path (Get-LogDir) 'backend.error.log'

# Hidden window so the application survives the launcher console closing.
# `--no-browser`: this script opens the browser, but only after health passes.
$process = Start-Process -FilePath $venvPython `
    -ArgumentList @('launcher.py', 'start', '--no-browser') `
    -WorkingDirectory (Get-RepoRoot) `
    -RedirectStandardOutput $backendLog `
    -RedirectStandardError $backendErr `
    -WindowStyle Hidden `
    -PassThru

if (-not $process) {
    Write-StageFailure -Stage 'Backend startup' -Command 'launcher.py start' -Detail 'The process could not be started.'
    exit 1
}

Save-TrackedProcess -PidFile $pidFile -Process $process
Write-Ok "Backend started (PID $($process.Id))"
Write-Host "    Logs: $backendLog" -ForegroundColor DarkGray

# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
Write-Stage 'Waiting for the health check'
$health = Wait-ForHealth -Url $url -TimeoutSeconds $TimeoutSeconds -Process $process

if (-not $health.healthy) {
    $detail = 'The backend did not report healthy.'
    if ($health.ContainsKey('reason')) { $detail = $health.reason }

    $tail = ''
    if (Test-Path $backendErr) { $tail = (Get-Content $backendErr -Tail 12 -ErrorAction SilentlyContinue) -join "`n" }
    if (-not $tail -and (Test-Path $backendLog)) { $tail = (Get-Content $backendLog -Tail 12 -ErrorAction SilentlyContinue) -join "`n" }

    Stop-TrackedProcess -PidFile $pidFile -Label 'backend' | Out-Null
    Write-StageFailure -Stage 'Health check' -Command "GET $url/health" -Detail $detail
    if ($tail) {
        Write-Host ''
        Write-Host 'Last backend output:' -ForegroundColor Red
        Write-Host $tail -ForegroundColor DarkGray
    }
    exit 1
}

Write-Ok "Health check passed (status: $($health.payload.status), version $($health.payload.version))"
Write-Host ("    {0,-24} {1}" -f 'database', $health.payload.database) -ForegroundColor DarkGray
Write-Host ("    {0,-24} {1}" -f 'dashboard', $health.payload.dashboard) -ForegroundColor DarkGray

# ---------------------------------------------------------------------------
# Browser
# ---------------------------------------------------------------------------
if (-not $NoBrowser) {
    Write-Stage 'Opening the dashboard'
    Open-Dashboard -Url $url
}

Write-Host ''
Write-Host ('=' * 64) -ForegroundColor Green
Write-Host ' AV TEST AUTOMATION DASHBOARD IS RUNNING' -ForegroundColor Green
Write-Host ('=' * 64) -ForegroundColor Green
Write-Host ''
Write-Host "  Dashboard:  $url"
Write-Host "  API docs:   $url/api/docs"
Write-Host "  Health:     $url/health"
Write-Host "  Logs:       $backendLog"
Write-Host ''
Write-Host '  Stop with:  STOP_AV_DASHBOARD.bat'
Write-Host ''
exit 0
