@echo off
setlocal

cd /d "%~dp0"

if "%TREND_RADAR_HOST%"=="" set "TREND_RADAR_HOST=127.0.0.1"
if "%TREND_RADAR_PORT%"=="" set "TREND_RADAR_PORT=8787"
set "DASHBOARD_URL=http://127.0.0.1:%TREND_RADAR_PORT%/"

echo Trend Radar dashboard is starting...
echo.
echo Keep this window open while using the dashboard.
echo.
echo If the browser does not open, visit:
echo %DASHBOARD_URL%
echo.

start "" "%DASHBOARD_URL%"

where py >nul 2>nul
if %ERRORLEVEL%==0 (
  py -3 backend\server.py %TREND_RADAR_PORT%
  goto :server_stopped
)

where python >nul 2>nul
if %ERRORLEVEL%==0 (
  python backend\server.py %TREND_RADAR_PORT%
  goto :server_stopped
)

echo Python was not found on this computer.
echo Please install Python 3.10 or later, and check "Add Python to PATH" during installation.
pause
goto :end

:server_stopped
echo.
echo The dashboard service stopped or failed to start.
echo If the browser says connection refused, check the messages above and run this file again.
echo Correct address: %DASHBOARD_URL%
pause

:end
endlocal
