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
rem Numeric comparison, not `if errorlevel`: see SETUP_AND_START.bat.
set "EXITCODE=%ERRORLEVEL%"

if not "%EXITCODE%"=="0" (
    echo.
    echo Stop reported a problem ^(exit code %EXITCODE%^).
    echo Review .runtime\logs\stop.log
    echo.
    pause
    exit /b %EXITCODE%
)

exit /b 0
