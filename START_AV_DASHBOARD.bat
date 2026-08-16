@echo off
setlocal
cd /d "%~dp0"

echo ==============================================
echo AV TEST AUTOMATION DASHBOARD
echo ==============================================
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\scripts\start_dashboard.ps1" %*
rem Numeric comparison, not `if errorlevel`: see SETUP_AND_START.bat.
set "EXITCODE=%ERRORLEVEL%"

if not "%EXITCODE%"=="0" (
    echo.
    echo The dashboard did not start ^(exit code %EXITCODE%^).
    echo Review the log for more information:
    echo   .runtime\logs\start.log
    echo   .runtime\logs\backend.error.log
    echo.
    echo If this is a new installation, run SETUP_AND_START.bat first.
    echo.
    pause
    exit /b %EXITCODE%
)

exit /b 0
