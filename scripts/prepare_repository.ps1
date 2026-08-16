<#
.SYNOPSIS
    Pre-commit gate: prove the repository is safe and working before it is pushed.

.DESCRIPTION
    Runs the security audit, the test suites, the type checks and the production
    build, then reports git status. If any SECURITY-critical check fails the
    script exits non-zero and the caller must not push.

    This script never stages, commits or pushes. It only decides whether doing so
    would be safe.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\prepare_repository.ps1

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\prepare_repository.ps1 -SkipTests
#>

[CmdletBinding()]
param(
    [switch]$SkipTests,
    [switch]$SkipBuild
)

. "$PSScriptRoot\common.ps1"

$ErrorActionPreference = 'Continue'
Set-Location (Get-RepoRoot)

$logFile = Initialize-Log -Name 'prepare'
Write-Banner 'REPOSITORY PREPARATION'

$venvPython = Get-VenvPython
$results = [ordered]@{}
$securityFailed = $false
$qualityFailed = $false

function Set-Result {
    param([string]$Name, [bool]$Passed, [switch]$Security, [string]$Detail = '')
    $results[$Name] = @{ passed = $Passed; detail = $Detail }
    if ($Passed) {
        Write-Ok "$Name $Detail"
    } else {
        Write-Fail "$Name $Detail"
        if ($Security) { $script:securityFailed = $true } else { $script:qualityFailed = $true }
    }
}

if (-not (Test-Path $venvPython)) {
    Write-StageFailure -Stage 'Repository preparation' -Command '.venv\Scripts\python.exe' `
        -Detail 'The virtual environment is missing. Run SETUP_AND_START.bat first.'
    exit 1
}

# ---------------------------------------------------------------------------
# 1. Security audit (blocking)
# ---------------------------------------------------------------------------
Write-Stage '1/7  Repository security audit'
& $venvPython (Join-Path $PSScriptRoot 'repository_audit.py') 2>&1 | Tee-Object -FilePath $logFile -Append | Out-Host
Set-Result -Name 'Security audit' -Passed ($LASTEXITCODE -eq 0) -Security

# ---------------------------------------------------------------------------
# 2. .gitignore sanity (blocking)
# ---------------------------------------------------------------------------
Write-Stage '2/7  Verifying .gitignore covers sensitive paths'
$mustBeIgnored = @('.env', 'data/local.db', 'output/results.csv', '.runtime/setup_state.json', 'secrets.json', 'id_rsa')
$notIgnored = @()
foreach ($path in $mustBeIgnored) {
    git check-ignore -q -- $path
    if ($LASTEXITCODE -ne 0) { $notIgnored += $path }
}
if ($notIgnored.Count -eq 0) {
    Set-Result -Name '.gitignore coverage' -Passed $true -Security -Detail "$($mustBeIgnored.Count) sensitive patterns confirmed ignored"
} else {
    Set-Result -Name '.gitignore coverage' -Passed $false -Security -Detail "NOT ignored: $($notIgnored -join ', ')"
}

# ---------------------------------------------------------------------------
# 3. Backend tests
# ---------------------------------------------------------------------------
Write-Stage '3/7  Backend tests'
if ($SkipTests) {
    Write-Skip 'Skipped by -SkipTests'
    $results['Backend tests'] = @{ passed = $true; detail = 'skipped' }
} else {
    & $venvPython -m pytest -q 2>&1 | Tee-Object -FilePath $logFile -Append | Out-Host
    Set-Result -Name 'Backend tests' -Passed ($LASTEXITCODE -eq 0)
}

# ---------------------------------------------------------------------------
# 4. Lint and type check
# ---------------------------------------------------------------------------
Write-Stage '4/7  Backend lint and type check'
& $venvPython -m ruff check . 2>&1 | Tee-Object -FilePath $logFile -Append | Out-Null
Set-Result -Name 'Backend lint (ruff)' -Passed ($LASTEXITCODE -eq 0)

& $venvPython -m mypy 2>&1 | Tee-Object -FilePath $logFile -Append | Out-Null
Set-Result -Name 'Backend types (mypy)' -Passed ($LASTEXITCODE -eq 0)

# ---------------------------------------------------------------------------
# 5. Frontend tests
# ---------------------------------------------------------------------------
Write-Stage '5/7  Dashboard tests'
$dashboardDir = Get-DashboardDir
$npm = Get-Command npm -ErrorAction SilentlyContinue
if (-not $npm -or -not (Test-Path (Join-Path $dashboardDir 'node_modules'))) {
    Write-Skip 'npm or node_modules unavailable'
    $results['Dashboard tests'] = @{ passed = $true; detail = 'skipped' }
} elseif ($SkipTests) {
    Write-Skip 'Skipped by -SkipTests'
    $results['Dashboard tests'] = @{ passed = $true; detail = 'skipped' }
} else {
    Push-Location $dashboardDir
    try {
        npm test 2>&1 | Tee-Object -FilePath $logFile -Append | Out-Host
        $testCode = $LASTEXITCODE
        npm run lint 2>&1 | Tee-Object -FilePath $logFile -Append | Out-Null
        $lintCode = $LASTEXITCODE
    } finally { Pop-Location }
    Set-Result -Name 'Dashboard tests' -Passed ($testCode -eq 0)
    Set-Result -Name 'Dashboard lint' -Passed ($lintCode -eq 0)
}

# ---------------------------------------------------------------------------
# 6. Frontend build
# ---------------------------------------------------------------------------
Write-Stage '6/7  Dashboard production build'
if ($SkipBuild -or -not $npm -or -not (Test-Path (Join-Path $dashboardDir 'node_modules'))) {
    Write-Skip 'Skipped'
    $results['Dashboard build'] = @{ passed = $true; detail = 'skipped' }
} else {
    Push-Location $dashboardDir
    try { npm run build 2>&1 | Tee-Object -FilePath $logFile -Append | Out-Null } finally { Pop-Location }
    $built = Test-Path (Join-Path $dashboardDir 'dist\index.html')
    Set-Result -Name 'Dashboard build' -Passed ($LASTEXITCODE -eq 0 -and $built)
}

# ---------------------------------------------------------------------------
# 7. Git status
# ---------------------------------------------------------------------------
Write-Stage '7/7  Git status'
git rev-parse --is-inside-work-tree 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-WarnMsg 'Not a git repository yet'
} else {
    $changes = @(git status --porcelain)
    Write-Host "    $($changes.Count) path(s) with changes" -ForegroundColor Gray
    $remotes = @(git remote -v)
    if ($remotes.Count -gt 0) {
        Write-Host '    Remotes:' -ForegroundColor Gray
        foreach ($remote in $remotes) { Write-Host "      $remote" -ForegroundColor Gray }
    } else {
        Write-Host '    No remote configured yet' -ForegroundColor Gray
    }
}

# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------
Write-Host ''
Write-Host ('=' * 64) -ForegroundColor White
Write-Host ' PREPARATION SUMMARY' -ForegroundColor White
Write-Host ('=' * 64) -ForegroundColor White
foreach ($name in $results.Keys) {
    $entry = $results[$name]
    $status = 'PASS'
    $colour = 'Green'
    if (-not $entry.passed) { $status = 'FAIL'; $colour = 'Red' }
    if ($entry.detail -eq 'skipped') { $status = 'SKIP'; $colour = 'DarkGray' }
    Write-Host ("  {0,-26}{1}" -f $name, $status) -ForegroundColor $colour
}
Write-Host ('=' * 64) -ForegroundColor White

if ($securityFailed) {
    Write-Host ''
    Write-Host ' SECURITY CHECK FAILED - DO NOT PUSH' -ForegroundColor Red
    Write-Host ' Resolve the findings above before staging anything.' -ForegroundColor Red
    Write-Host ''
    exit 1
}
if ($qualityFailed) {
    Write-Host ''
    Write-Host ' Quality checks failed. The repository is SAFE to commit, but the' -ForegroundColor Yellow
    Write-Host ' build or tests are broken. Fix them before pushing.' -ForegroundColor Yellow
    Write-Host ''
    exit 2
}

Write-Host ''
Write-Host ' Repository is safe and healthy to commit and push.' -ForegroundColor Green
Write-Host ''
exit 0
