<#
.SYNOPSIS
    Validate that this workstation can run the AV Test Automation Platform.

.DESCRIPTION
    Prints a PASS / WARN / FAIL table and returns a non-zero exit code only when
    something genuinely blocks installation. GPU and CUDA are OPTIONAL: nothing
    in the metadata, geometry, validation or export path needs them.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\validate_environment.ps1
#>

[CmdletBinding()]
param([switch]$Quiet)

. "$PSScriptRoot\common.ps1"

$ErrorActionPreference = 'Continue'

$script:Failures = 0
$script:Warnings = 0

function Add-Check {
    param([string]$Name, [string]$Status, [string]$Detail = '')
    if ($Status -eq 'FAIL' -or $Status -eq 'MISSING') { $script:Failures++ }
    if ($Status -eq 'WARN') { $script:Warnings++ }
    if (-not $Quiet) { Write-CheckLine -Name $Name -Status $Status -Detail $Detail }
}

function Get-CommandVersion {
    param([string]$Command, [string[]]$VersionArgs = @('--version'))
    $found = Get-Command $Command -ErrorAction SilentlyContinue
    if (-not $found) { return $null }
    try {
        $output = & $Command @VersionArgs 2>&1 | Select-Object -First 1
        return [string]$output
    } catch {
        return 'present'
    }
}

if (-not $Quiet) {
    Write-Host ''
    Write-Host ('=' * 64) -ForegroundColor White
    Write-Host ' SYSTEM VALIDATION' -ForegroundColor White
    Write-Host ('=' * 64) -ForegroundColor White
    Write-Host ''
}

# --- Operating system -------------------------------------------------------
try {
    $os = Get-CimInstance Win32_OperatingSystem -ErrorAction Stop
    $caption = $os.Caption.Trim()
    $build = [int]($os.BuildNumber)
    if ($build -ge 10240) {
        Add-Check 'Windows' 'PASS' "$caption (build $build)"
    } else {
        Add-Check 'Windows' 'WARN' "$caption (build $build) is older than tested"
    }
} catch {
    Add-Check 'Windows' 'WARN' 'Could not determine the Windows version'
}

# --- Architecture -----------------------------------------------------------
$architecture = $env:PROCESSOR_ARCHITECTURE
if ($architecture -eq 'AMD64' -or $architecture -eq 'ARM64') {
    Add-Check 'Architecture' 'PASS' $architecture
} else {
    Add-Check 'Architecture' 'FAIL' "$architecture is not supported (x64 or ARM64 required)"
}

# --- Memory -----------------------------------------------------------------
try {
    $memoryBytes = (Get-CimInstance Win32_ComputerSystem -ErrorAction Stop).TotalPhysicalMemory
    $memoryGb = [math]::Round($memoryBytes / 1GB, 1)
    if ($memoryGb -ge 8)      { Add-Check 'RAM' 'PASS' "$memoryGb GB" }
    elseif ($memoryGb -ge 4)  { Add-Check 'RAM' 'WARN' "$memoryGb GB (8 GB recommended)" }
    else                      { Add-Check 'RAM' 'FAIL' "$memoryGb GB (4 GB minimum)" }
} catch {
    Add-Check 'RAM' 'WARN' 'Could not determine installed memory'
}

# --- Disk -------------------------------------------------------------------
try {
    $repoRoot = Get-RepoRoot
    $driveLetter = (Split-Path -Qualifier $repoRoot).TrimEnd(':')
    $drive = Get-PSDrive -Name $driveLetter -ErrorAction Stop
    $freeGb = [math]::Round($drive.Free / 1GB, 1)
    if ($freeGb -ge 5)     { Add-Check 'Disk Space' 'PASS' "$freeGb GB free on ${driveLetter}:" }
    elseif ($freeGb -ge 2) { Add-Check 'Disk Space' 'WARN' "$freeGb GB free (5 GB recommended)" }
    else                   { Add-Check 'Disk Space' 'FAIL' "$freeGb GB free (2 GB minimum)" }
} catch {
    Add-Check 'Disk Space' 'WARN' 'Could not determine free disk space'
}

# --- Git --------------------------------------------------------------------
$gitVersion = Get-CommandVersion 'git'
if ($gitVersion) {
    Add-Check 'Git' 'PASS' $gitVersion
} else {
    # Git is only needed to clone and update, not to run the application.
    Add-Check 'Git' 'WARN' 'Not found - UPDATE_AND_START.bat will not work'
}

# --- Python -----------------------------------------------------------------
$pythonFound = $false
$pythonCommand = $null
foreach ($candidate in @('python', 'py')) {
    $raw = Get-CommandVersion $candidate
    if ($raw -and $raw -match 'Python (\d+)\.(\d+)\.(\d+)') {
        $major = [int]$Matches[1]; $minor = [int]$Matches[2]
        if ($major -eq 3 -and $minor -ge 10) {
            $pythonFound = $true
            $pythonCommand = $candidate
            if ($minor -ge 11) {
                Add-Check 'Python' 'PASS' "$raw (via '$candidate')"
            } else {
                Add-Check 'Python' 'PASS' "$raw (3.11+ recommended, 3.$minor supported)"
            }
            break
        }
    }
}
if (-not $pythonFound) {
    Add-Check 'Python' 'FAIL' 'Python 3.10 or newer was not found on PATH'
}

# --- Node and npm -----------------------------------------------------------
$nodeVersion = Get-CommandVersion 'node'
if ($nodeVersion -and $nodeVersion -match 'v(\d+)\.') {
    $nodeMajor = [int]$Matches[1]
    if ($nodeMajor -ge 18) {
        Add-Check 'Node.js' 'PASS' $nodeVersion
    } else {
        Add-Check 'Node.js' 'WARN' "$nodeVersion (18+ required to build the dashboard)"
    }
} else {
    # The API and every backend workflow run without Node; only the UI build needs it.
    Add-Check 'Node.js' 'WARN' 'Not found - the dashboard cannot be rebuilt on this machine'
}

$npmVersion = Get-CommandVersion 'npm'
if ($npmVersion) {
    Add-Check 'npm' 'PASS' $npmVersion
} else {
    Add-Check 'npm' 'WARN' 'Not found - the dashboard cannot be rebuilt on this machine'
}

# --- Application port -------------------------------------------------------
$port = Get-AppPort
if (Test-PortFree -Port $port) {
    Add-Check "Port $port" 'PASS' 'available'
} else {
    $ours = Get-TrackedProcess -PidFile (Get-BackendPidFile)
    if ($ours) {
        Add-Check "Port $port" 'PASS' "in use by this application (PID $($ours.Id))"
    } else {
        Add-Check "Port $port" 'WARN' 'in use by another process - set AV_PORT in .env to change it'
    }
}

# --- Repository integrity ---------------------------------------------------
$required = @('backend\main.py', 'requirements.txt', 'config\base.yaml', 'dashboard\package.json', 'launcher.py')
$missing = @()
foreach ($relative in $required) {
    if (-not (Test-Path (Join-Path (Get-RepoRoot) $relative))) { $missing += $relative }
}
if ($missing.Count -eq 0) {
    Add-Check 'Repository' 'PASS' 'all expected project files present'
} else {
    Add-Check 'Repository' 'FAIL' "missing: $($missing -join ', ')"
}

# --- GPU and CUDA (optional) ------------------------------------------------
$nvidiaSmi = Get-Command 'nvidia-smi' -ErrorAction SilentlyContinue
if ($nvidiaSmi) {
    # No `2>&1` here: in Windows PowerShell 5.1 redirecting a native command's
    # stderr wraps every line in an ErrorRecord and trips $?, which made this
    # check report "no device" on a machine that has one.
    $gpuLines = @(& nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader)
    $gpuOk = ($LASTEXITCODE -eq 0)
    $firstGpu = ''
    if ($gpuLines.Count -gt 0) { $firstGpu = ([string]$gpuLines[0]).Trim() }

    if ($gpuOk -and $firstGpu) {
        $fields = $firstGpu -split '\s*,\s*'
        $name = $fields[0]
        $memory = ''
        if ($fields.Count -gt 1) { $memory = $fields[1] }
        $driver = ''
        if ($fields.Count -gt 2) { $driver = $fields[2] }

        Add-Check 'NVIDIA GPU' 'AVAILABLE' "$name ($memory)"
        if ($driver) {
            Add-Check 'CUDA' 'AVAILABLE' "driver $driver"
        } else {
            Add-Check 'CUDA' 'OPTIONAL' 'driver version unavailable'
        }
    } else {
        Add-Check 'NVIDIA GPU' 'OPTIONAL' 'nvidia-smi present but reported no device'
        Add-Check 'CUDA' 'OPTIONAL' 'not available - not required for this platform'
    }
} else {
    Add-Check 'NVIDIA GPU' 'OPTIONAL' 'not detected - not required for this platform'
    Add-Check 'CUDA' 'OPTIONAL' 'not detected - not required for this platform'
}

# --- Corporate proxy --------------------------------------------------------
if ($env:HTTPS_PROXY -or $env:HTTP_PROXY) {
    $proxy = $env:HTTPS_PROXY
    if (-not $proxy) { $proxy = $env:HTTP_PROXY }
    Add-Check 'Proxy' 'PASS' "configured ($proxy) - will be honoured"
}

if (-not $Quiet) {
    Write-Host ''
    Write-Host ('=' * 64) -ForegroundColor White
    if ($script:Failures -gt 0) {
        Write-Host " VALIDATION FAILED: $($script:Failures) blocking issue(s), $($script:Warnings) warning(s)" -ForegroundColor Red
    } elseif ($script:Warnings -gt 0) {
        Write-Host " VALIDATION PASSED with $($script:Warnings) warning(s)" -ForegroundColor Yellow
    } else {
        Write-Host ' VALIDATION PASSED' -ForegroundColor Green
    }
    Write-Host ('=' * 64) -ForegroundColor White
}

if ($script:Failures -gt 0) { exit 1 }
exit 0
