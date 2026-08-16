<#
.SYNOPSIS
    Stop the AV Test Automation Dashboard.

.DESCRIPTION
    Stops ONLY processes this installation started and can still positively
    identify, verified by recorded PID, process start time, image path and
    command line.

    It deliberately never runs anything like `taskkill /IM python.exe /F`.
    Windows reuses PIDs, and a tester's other Python or Node work must survive
    stopping this dashboard.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\stop_dashboard.ps1
#>

[CmdletBinding()]
param()

. "$PSScriptRoot\common.ps1"

$ErrorActionPreference = 'Stop'
Set-Location (Get-RepoRoot)

Initialize-Log -Name 'stop' | Out-Null
Write-Banner 'STOPPING THE AV TEST AUTOMATION DASHBOARD'

$stopped = 0
$pidFiles = @(
    @{ File = (Get-BackendPidFile); Label = 'backend' },
    @{ File = (Join-Path (Get-RepoRoot) '.runtime\pids\frontend.pid'); Label = 'frontend dev server' }
)

foreach ($entry in $pidFiles) {
    if (-not (Test-Path $entry.File)) { continue }

    $process = Get-TrackedProcess -PidFile $entry.File
    if (-not $process) {
        # The PID file is stale: the process is gone, or the PID now belongs to
        # something else. Either way, removing the file is the only safe action.
        Write-Skip "$($entry.Label): recorded process is no longer ours (stale PID file removed)"
        Remove-PidFile -PidFile $entry.File
        continue
    }

    if (Stop-TrackedProcess -PidFile $entry.File -Label $entry.Label) {
        Write-Ok "$($entry.Label) stopped"
        $stopped++
    }
}

$port = Get-AppPort
if ($stopped -eq 0) {
    Write-Host ''
    Write-Host '  Nothing was running that this installation owns.' -ForegroundColor Gray

    if (-not (Test-PortFree -Port $port)) {
        Write-Host ''
        Write-WarnMsg "Port $port is still in use by a process this installation did not start."
        Write-Host '    It has been left alone deliberately. Identify it with:' -ForegroundColor Gray
        Write-Host "      Get-NetTCPConnection -LocalPort $port -State Listen | Select-Object OwningProcess" -ForegroundColor Gray
    }
} else {
    Write-Host ''
    Write-Host ('=' * 64) -ForegroundColor Green
    Write-Host ' DASHBOARD STOPPED' -ForegroundColor Green
    Write-Host ('=' * 64) -ForegroundColor Green
}

Write-Host ''
exit 0
