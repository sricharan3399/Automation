@echo off
setlocal
cd /d "%~dp0"

echo ==============================================
echo AV TEST AUTOMATION DASHBOARD
echo ==============================================
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\scripts\start_dashboard.ps1" %*

if errorlevel 1 (
    echo.
    echo The dashboard did not start.
    echo Review the log for more information:
    echo   .runtime\logs\start.log
    echo   .runtime\logs\backend.error.log
    echo.
    echo If this is a new installation, run SETUP_AND_START.bat first.
    echo.
    pause
    exit /b 1
)

exit /b 0
