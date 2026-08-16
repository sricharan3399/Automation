<#
.SYNOPSIS
    Shared helpers for the Windows deployment scripts.

.DESCRIPTION
    Dot-source this from every deployment script:

        . "$PSScriptRoot\common.ps1"

    Targets Windows PowerShell 5.1, so no ternary, null-coalescing or
    null-conditional operators are used anywhere in these scripts.
#>

Set-StrictMode -Version Latest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
$script:RepoRoot     = Split-Path -Parent $PSScriptRoot
$script:RuntimeDir   = Join-Path $script:RepoRoot '.runtime'
$script:LogDir       = Join-Path $script:RuntimeDir 'logs'
$script:PidDir       = Join-Path $script:RuntimeDir 'pids'
$script:BackupDir    = Join-Path $script:RuntimeDir 'backups'
$script:StateFile    = Join-Path $script:RuntimeDir 'setup_state.json'
$script:VenvPython   = Join-Path $script:RepoRoot '.venv\Scripts\python.exe'
$script:BackendPid   = Join-Path $script:PidDir 'backend.pid'
$script:DashboardDir = Join-Path $script:RepoRoot 'dashboard'

function Get-RepoRoot { return $script:RepoRoot }
function Get-VenvPython { return $script:VenvPython }
function Get-BackendPidFile { return $script:BackendPid }
function Get-StateFile { return $script:StateFile }
function Get-LogDir { return $script:LogDir }
function Get-BackupDir { return $script:BackupDir }
function Get-DashboardDir { return $script:DashboardDir }

# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
$script:LogFile = $null

function Initialize-Log {
    param([string]$Name = 'setup')
    if (-not (Test-Path $script:LogDir)) {
        New-Item -ItemType Directory -Path $script:LogDir -Force | Out-Null
    }
    $script:LogFile = Join-Path $script:LogDir "$Name.log"
    $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    Add-Content -Path $script:LogFile -Value "`n===== $Name started $stamp =====" -Encoding utf8
    return $script:LogFile
}

function Write-Log {
    param([string]$Message)
    if ($script:LogFile) {
        $stamp = Get-Date -Format 'HH:mm:ss'
        Add-Content -Path $script:LogFile -Value "[$stamp] $Message" -Encoding utf8
    }
}

function Write-Banner {
    param([string]$Text)
    Write-Host ''
    Write-Host ('=' * 64) -ForegroundColor White
    Write-Host " $Text" -ForegroundColor White
    Write-Host ('=' * 64) -ForegroundColor White
    Write-Log "== $Text =="
}

function Write-Stage {
    param([string]$Text)
    Write-Host ''
    Write-Host "==> $Text" -ForegroundColor Cyan
    Write-Log "STAGE: $Text"
}

function Write-Ok {
    param([string]$Text)
    Write-Host ("    {0,-24} {1}" -f 'OK', $Text) -ForegroundColor Green
    Write-Log "OK: $Text"
}

function Write-Skip {
    param([string]$Text)
    Write-Host ("    {0,-24} {1}" -f 'SKIP', $Text) -ForegroundColor DarkGray
    Write-Log "SKIP: $Text"
}

function Write-WarnMsg {
    param([string]$Text)
    Write-Host ("    {0,-24} {1}" -f 'WARN', $Text) -ForegroundColor Yellow
    Write-Log "WARN: $Text"
}

function Write-Fail {
    param([string]$Text)
    Write-Host ("    {0,-24} {1}" -f 'FAIL', $Text) -ForegroundColor Red
    Write-Log "FAIL: $Text"
}

function Write-CheckLine {
    param([string]$Name, [string]$Status, [string]$Detail = '')
    $colour = 'Green'
    if ($Status -eq 'WARN' -or $Status -eq 'OPTIONAL') { $colour = 'Yellow' }
    if ($Status -eq 'FAIL' -or $Status -eq 'MISSING')  { $colour = 'Red' }
    $line = "  {0,-26}{1,-12}{2}" -f $Name, $Status, $Detail
    Write-Host $line -ForegroundColor $colour
    Write-Log "CHECK $Name = $Status $Detail"
}

<#
Report a failed stage with the command, the log location and an explicit
statement that the application was not started. Never prints a fake PASS.
#>
function Write-StageFailure {
    param(
        [string]$Stage,
        [string]$Command,
        [string]$Detail = ''
    )
    $log = 'not created'
    if ($script:LogFile) { $log = $script:LogFile }
    Write-Host ''
    Write-Host ('=' * 64) -ForegroundColor Red
    Write-Host ' AV DASHBOARD STARTUP FAILED' -ForegroundColor Red
    Write-Host ('=' * 64) -ForegroundColor Red
    Write-Host ''
    Write-Host 'Stage:'   -ForegroundColor Red; Write-Host "  $Stage"
    Write-Host 'Command:' -ForegroundColor Red; Write-Host "  $Command"
    Write-Host 'Result:'  -ForegroundColor Red; Write-Host '  FAILED'
    if ($Detail) { Write-Host 'Detail:' -ForegroundColor Red; Write-Host "  $Detail" }
    Write-Host 'Log:'     -ForegroundColor Red; Write-Host "  $log"
    Write-Host ''
    Write-Host 'The application was not started.' -ForegroundColor Red
    Write-Host ('=' * 64) -ForegroundColor Red
    Write-Log "STAGE FAILURE: $Stage | $Command | $Detail"
}

# ---------------------------------------------------------------------------
# Runtime directories
# ---------------------------------------------------------------------------
function Initialize-RuntimeDirectories {
    # Only the directories this project actually uses.
    $dirs = @(
        $script:RuntimeDir,
        $script:LogDir,
        $script:PidDir,
        $script:BackupDir,
        (Join-Path $script:RepoRoot 'data'),
        (Join-Path $script:RepoRoot 'data\checkpoints'),
        (Join-Path $script:RepoRoot 'data\cache'),
        (Join-Path $script:RepoRoot 'output')
    )
    $created = 0
    foreach ($dir in $dirs) {
        if (-not (Test-Path $dir)) {
            New-Item -ItemType Directory -Path $dir -Force | Out-Null
            $created++
        }
    }
    return $created
}

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
<#
Resolve the port the application will actually bind.
Precedence matches backend/settings.py: environment, then .env, then
config/base.yaml, then the documented default.
#>
function Get-AppPort {
    if ($env:AV_PORT) { return [int]$env:AV_PORT }

    $envFile = Join-Path $script:RepoRoot '.env'
    if (Test-Path $envFile) {
        $match = Select-String -Path $envFile -Pattern '^\s*AV_PORT\s*=\s*(\d+)' -ErrorAction SilentlyContinue |
                 Select-Object -First 1
        if ($match) { return [int]$match.Matches[0].Groups[1].Value }
    }

    $baseConfig = Join-Path $script:RepoRoot 'config\base.yaml'
    if (Test-Path $baseConfig) {
        $match = Select-String -Path $baseConfig -Pattern '^\s{2}port:\s*(\d+)' -ErrorAction SilentlyContinue |
                 Select-Object -First 1
        if ($match) { return [int]$match.Matches[0].Groups[1].Value }
    }
    return 8000
}

function Get-AppHost {
    if ($env:AV_HOST) { return $env:AV_HOST }
    $envFile = Join-Path $script:RepoRoot '.env'
    if (Test-Path $envFile) {
        $match = Select-String -Path $envFile -Pattern '^\s*AV_HOST\s*=\s*(\S+)' -ErrorAction SilentlyContinue |
                 Select-Object -First 1
        if ($match) { return $match.Matches[0].Groups[1].Value }
    }
    # Bind loopback by default: this dashboard must not be reachable from the LAN.
    return '127.0.0.1'
}

function Get-AppUrl {
    $appHost = Get-AppHost
    $port = Get-AppPort
    if ($appHost -eq '0.0.0.0') { $appHost = '127.0.0.1' }
    return "http://${appHost}:${port}"
}

<#
Create .env from .env.example on first run. Never overwrites an existing .env,
because that file may hold a tester's local configuration.
#>
function Initialize-EnvFile {
    $envFile = Join-Path $script:RepoRoot '.env'
    $template = Join-Path $script:RepoRoot '.env.example'

    if (Test-Path $envFile) {
        Write-Skip 'Local .env already exists (left untouched)'
        return $false
    }
    if (-not (Test-Path $template)) {
        Write-WarnMsg '.env.example is missing; the application will use built-in defaults'
        return $false
    }

    Copy-Item $template $envFile
    Write-Ok 'Created .env from .env.example'
    Write-Host ''
    Write-Host '    Local configuration created.' -ForegroundColor Gray
    Write-Host ''
    Write-Host '    Data Scout credentials are not included in GitHub.' -ForegroundColor Gray
    Write-Host '    Configure approved internal connection information through the' -ForegroundColor Gray
    Write-Host '    dashboard or an approved local secret mechanism.' -ForegroundColor Gray
    Write-Host ''
    return $true
}

# ---------------------------------------------------------------------------
# Setup state and dependency hashing
# ---------------------------------------------------------------------------
function Get-SetupState {
    if (-not (Test-Path $script:StateFile)) {
        return [ordered]@{
            setup_completed        = $false
            backend_dependencies   = $false
            frontend_dependencies  = $false
            frontend_built         = $false
            database_initialized   = $false
            hashes                 = @{}
        }
    }
    try {
        $raw = Get-Content $script:StateFile -Raw -Encoding utf8
        $parsed = $raw | ConvertFrom-Json
        $state = [ordered]@{
            setup_completed       = [bool]$parsed.setup_completed
            backend_dependencies  = [bool]$parsed.backend_dependencies
            frontend_dependencies = [bool]$parsed.frontend_dependencies
            frontend_built        = [bool]$parsed.frontend_built
            database_initialized  = [bool]$parsed.database_initialized
            hashes                = @{}
        }
        if ($parsed.PSObject.Properties.Name -contains 'hashes' -and $parsed.hashes) {
            foreach ($property in $parsed.hashes.PSObject.Properties) {
                $state.hashes[$property.Name] = $property.Value
            }
        }
        return $state
    } catch {
        Write-WarnMsg 'setup_state.json is unreadable and will be rebuilt'
        return [ordered]@{
            setup_completed = $false; backend_dependencies = $false
            frontend_dependencies = $false; frontend_built = $false
            database_initialized = $false; hashes = @{}
        }
    }
}

function Save-SetupState {
    param($State)
    if (-not (Test-Path $script:RuntimeDir)) {
        New-Item -ItemType Directory -Path $script:RuntimeDir -Force | Out-Null
    }
    ($State | ConvertTo-Json -Depth 6) | Set-Content -Path $script:StateFile -Encoding utf8
}

function Get-FileHashOrEmpty {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return '' }
    try { return (Get-FileHash -Path $Path -Algorithm SHA256).Hash } catch { return '' }
}

<#
Combined hash of the files that determine whether a dependency set is current.
Comparing this against the stored value is what lets a normal startup skip
reinstalling everything.
#>
function Get-DependencyHash {
    param([string[]]$Files)
    $parts = @()
    foreach ($file in $Files) {
        $parts += (Get-FileHashOrEmpty (Join-Path $script:RepoRoot $file))
    }
    $joined = ($parts -join '|')
    if (-not $joined.Replace('|', '')) { return '' }
    $stream = [System.IO.MemoryStream]::new([System.Text.Encoding]::UTF8.GetBytes($joined))
    try { return (Get-FileHash -InputStream $stream -Algorithm SHA256).Hash } finally { $stream.Dispose() }
}

function Get-PythonDependencyHash   { return Get-DependencyHash @('requirements.txt', 'requirements-dev.txt', 'pyproject.toml') }
function Get-NodeDependencyHash     { return Get-DependencyHash @('dashboard\package.json', 'dashboard\package-lock.json') }

<#
Hash of the frontend sources that feed the production build, so the build is
rebuilt when the UI changes and skipped when it has not.
#>
function Get-FrontendSourceHash {
    $srcDir = Join-Path $script:DashboardDir 'src'
    if (-not (Test-Path $srcDir)) { return '' }
    $files = Get-ChildItem $srcDir -Recurse -File | Sort-Object FullName
    $builder = New-Object System.Text.StringBuilder
    foreach ($file in $files) {
        [void]$builder.Append($file.FullName.Substring($script:RepoRoot.Length))
        [void]$builder.Append((Get-FileHash $file.FullName -Algorithm SHA256).Hash)
    }
    foreach ($extra in @('index.html', 'vite.config.ts', 'tsconfig.json')) {
        $path = Join-Path $script:DashboardDir $extra
        if (Test-Path $path) { [void]$builder.Append((Get-FileHash $path -Algorithm SHA256).Hash) }
    }
    $stream = [System.IO.MemoryStream]::new([System.Text.Encoding]::UTF8.GetBytes($builder.ToString()))
    try { return (Get-FileHash -InputStream $stream -Algorithm SHA256).Hash } finally { $stream.Dispose() }
}

# ---------------------------------------------------------------------------
# Process management
# ---------------------------------------------------------------------------
<#
Record a PID together with enough identity to prove later that the process we
find is still the one we started. A PID alone is not enough: Windows reuses
them, and killing a recycled PID would stop an unrelated program.
#>
function Save-TrackedProcess {
    param(
        [string]$PidFile,
        [System.Diagnostics.Process]$Process
    )
    $dir = Split-Path -Parent $PidFile
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    $record = [ordered]@{
        pid        = $Process.Id
        started_at = $Process.StartTime.ToString('o')
        image      = $Process.Path
        repo_root  = $script:RepoRoot
    }
    ($record | ConvertTo-Json) | Set-Content -Path $PidFile -Encoding utf8
}

<#
Return the tracked process only if it is still alive AND still ours.

Verified by start time, image path and command line. Anything less risks
stopping an unrelated python.exe that happened to inherit the PID.
#>
function Get-TrackedProcess {
    param([string]$PidFile)
    if (-not (Test-Path $PidFile)) { return $null }

    try {
        $record = Get-Content $PidFile -Raw -Encoding utf8 | ConvertFrom-Json
    } catch {
        return $null
    }
    if (-not $record.pid) { return $null }

    $process = Get-Process -Id $record.pid -ErrorAction SilentlyContinue
    if (-not $process) { return $null }

    # Start time must match: a recycled PID belongs to a different process.
    try {
        $recorded = [datetime]::Parse($record.started_at)
        if ([math]::Abs(($process.StartTime - $recorded).TotalSeconds) -gt 2) { return $null }
    } catch {
        return $null
    }

    # And it must be this repository's interpreter running this application.
    try {
        $cim = Get-CimInstance Win32_Process -Filter "ProcessId = $($record.pid)" -ErrorAction Stop
        $commandLine = $cim.CommandLine
        if (-not $commandLine) { return $null }
        if ($commandLine -notlike "*$($record.repo_root)*" -and $commandLine -notlike '*backend.main*' -and $commandLine -notlike '*launcher.py*') {
            return $null
        }
    } catch {
        return $null
    }

    return $process
}

function Remove-PidFile {
    param([string]$PidFile)
    if (Test-Path $PidFile) { Remove-Item $PidFile -Force -ErrorAction SilentlyContinue }
}

<#
Stop a tracked process gracefully, escalating only if it refuses to exit.
Never touches a process that Get-TrackedProcess did not positively identify.
#>
function Stop-TrackedProcess {
    param(
        [string]$PidFile,
        [string]$Label = 'process'
    )
    $process = Get-TrackedProcess -PidFile $PidFile
    if (-not $process) {
        Remove-PidFile -PidFile $PidFile
        return $false
    }

    Write-Host "    Stopping $Label (PID $($process.Id))..." -ForegroundColor Gray
    try {
        $process.CloseMainWindow() | Out-Null
    } catch {
        # No window to close; fall through to the wait-and-terminate path.
    }

    $deadline = (Get-Date).AddSeconds(10)
    while ((Get-Date) -lt $deadline) {
        if ($process.HasExited) { break }
        Start-Sleep -Milliseconds 300
        $process.Refresh()
    }

    if (-not $process.HasExited) {
        Write-Log "$Label did not exit gracefully; terminating PID $($process.Id)"
        Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 500
    }

    Remove-PidFile -PidFile $PidFile
    return $true
}

# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
<#
Poll the real health endpoint until it reports healthy or the timeout expires.
Starting a process and assuming success is not a health check.
#>
function Wait-ForHealth {
    param(
        [string]$Url,
        [int]$TimeoutSeconds = 90,
        [System.Diagnostics.Process]$Process = $null
    )
    $endpoint = "$Url/health"
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $attempt = 0

    while ((Get-Date) -lt $deadline) {
        $attempt++

        # If the backend died, stop waiting and say so immediately.
        if ($Process -and $Process.HasExited) {
            Write-Log "backend exited with code $($Process.ExitCode) while waiting for health"
            return @{ healthy = $false; reason = "The backend exited with code $($Process.ExitCode) during startup." }
        }

        try {
            $response = Invoke-WebRequest -Uri $endpoint -UseBasicParsing -TimeoutSec 5
            if ($response.StatusCode -eq 200) {
                $payload = $response.Content | ConvertFrom-Json
                if ($payload.status -eq 'healthy') {
                    return @{ healthy = $true; payload = $payload }
                }
                return @{ healthy = $false; reason = "Health endpoint reported '$($payload.status)'." ; payload = $payload }
            }
        } catch {
            # Not up yet. Keep polling until the deadline.
        }

        if ($attempt % 5 -eq 0) { Write-Host '    waiting for the backend to become healthy...' -ForegroundColor DarkGray }
        Start-Sleep -Milliseconds 800
    }
    return @{ healthy = $false; reason = "The backend did not become healthy within $TimeoutSeconds seconds." }
}

function Test-PortFree {
    param([int]$Port)
    try {
        $connections = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
        return (-not $connections)
    } catch {
        # Get-NetTCPConnection is unavailable on some SKUs; fall back to a probe.
        try {
            $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Port)
            $listener.Start(); $listener.Stop()
            return $true
        } catch {
            return $false
        }
    }
}

function Open-Dashboard {
    param([string]$Url)
    try {
        Start-Process $Url | Out-Null
        Write-Ok "Opened $Url"
    } catch {
        Write-WarnMsg "Could not open a browser automatically. Open $Url manually."
    }
}
