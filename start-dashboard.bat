@echo off
setlocal

cd /d "%~dp0"

if "%TREND_RADAR_HOST%"=="" set "TREND_RADAR_HOST=127.0.0.1"
if "%TREND_RADAR_PORT%"=="" set "TREND_RADAR_PORT=18787"
set "TREND_RADAR_OPEN_BROWSER=1"
set "DASHBOARD_URL=http://127.0.0.1:%TREND_RADAR_PORT%/"

echo Trend Radar dashboard is starting...
echo.
echo Keep this window open while using the dashboard.
echo.
echo If the browser does not open, visit:
echo %DASHBOARD_URL%
echo.

if not exist "backend\server.py" (
  echo Cannot find backend\server.py.
  echo Please unzip the downloaded ZIP first, then run start-dashboard.bat from the extracted folder.
  pause
  goto :end
)

set "PYTHON_CMD="
set "PYTHON_LABEL="

py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if not errorlevel 1 (
  set "PYTHON_CMD=py -3"
  set "PYTHON_LABEL=py -3"
)

if not defined PYTHON_CMD (
  python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
  if not errorlevel 1 (
    set "PYTHON_CMD=python"
    set "PYTHON_LABEL=python"
  )
)

if defined PYTHON_CMD (
  echo Using Python command: %PYTHON_LABEL%
  echo.
  %PYTHON_CMD% backend\server.py %TREND_RADAR_PORT%
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
