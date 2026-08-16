@echo off
setlocal
cd /d "%~dp0"

echo ==============================================
echo AV TEST AUTOMATION DASHBOARD SETUP
echo ==============================================
echo.
echo This performs first-time installation:
echo   validate PC, create virtual environment, install
echo   dependencies, build dashboard, initialise database,
echo   start backend, health check, open browser.
echo.
echo No data source is contacted during setup.
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\scripts\bootstrap_windows.ps1" %*

if errorlevel 1 (
    echo.
    echo Setup failed.
    echo Review the setup log for more information:
    echo   .runtime\logs\setup.log
    echo.
    pause
    exit /b 1
)

echo.
echo Setup complete. Use START_AV_DASHBOARD.bat for normal startup.
echo.
pause
exit /b 0
