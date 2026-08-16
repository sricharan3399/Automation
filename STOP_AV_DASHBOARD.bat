@echo off
setlocal
cd /d "%~dp0"

echo ==============================================
echo STOPPING AV TEST AUTOMATION DASHBOARD
echo ==============================================
echo.
echo Only processes started by this installation are stopped.
echo Other Python and Node processes are left alone.
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\scripts\stop_dashboard.ps1"

if errorlevel 1 (
    echo.
    echo Stop reported a problem. Review .runtime\logs\stop.log
    echo.
    pause
    exit /b 1
)

exit /b 0
