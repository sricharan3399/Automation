@echo off
setlocal
cd /d "%~dp0"

echo ==============================================
echo AV TEST AUTOMATION - TEST SUITE
echo ==============================================
echo.
echo Runs the security audit, backend tests, lint, type
echo checks, dashboard tests and the production build.
echo.
echo Everything runs offline against the synthetic golden
echo dataset. No data source is contacted.
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\scripts\prepare_repository.ps1" %*
rem Numeric comparison, not `if errorlevel`: see SETUP_AND_START.bat.
set "EXITCODE=%ERRORLEVEL%"

if "%EXITCODE%"=="1" (
    echo.
    echo SECURITY CHECK FAILED - do not push.
    echo.
    pause
    exit /b 1
)

if "%EXITCODE%"=="2" (
    echo.
    echo Quality checks failed. See .runtime\logs\prepare.log
    echo.
    pause
    exit /b 2
)

rem Catch-all so an unexpected or negative code is never reported as success.
if not "%EXITCODE%"=="0" (
    echo.
    echo Test run ended with unexpected exit code %EXITCODE%.
    echo It did not complete. See .runtime\logs\prepare.log
    echo.
    pause
    exit /b %EXITCODE%
)

echo.
pause
exit /b 0
