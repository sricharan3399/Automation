@echo off
setlocal
cd /d "%~dp0"

echo ==============================================
echo UPDATING AV TEST AUTOMATION DASHBOARD
echo ==============================================
echo.
echo Pulls the newest approved version from GitHub and
echo restarts. Local source modifications stop the update
echo rather than being discarded.
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\scripts\update_and_start.ps1" %*

if errorlevel 1 (
    echo.
    echo Update did not complete.
    echo Review the log for more information:
    echo   .runtime\logs\update.log
    echo.
    pause
    exit /b 1
)

exit /b 0
