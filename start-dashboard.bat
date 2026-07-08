@echo off
setlocal

cd /d "%~dp0"

if "%TREND_RADAR_HOST%"=="" set TREND_RADAR_HOST=127.0.0.1
if "%TREND_RADAR_PORT%"=="" set TREND_RADAR_PORT=8787

echo Trend Radar is starting...
echo.
echo Open this address in your browser:
echo http://127.0.0.1:%TREND_RADAR_PORT%
echo.
echo Keep this window open while using the dashboard.
echo.

where py >nul 2>nul
if %ERRORLEVEL%==0 (
  py -3 backend\server.py %TREND_RADAR_PORT%
  goto :end
)

where python >nul 2>nul
if %ERRORLEVEL%==0 (
  python backend\server.py %TREND_RADAR_PORT%
  goto :end
)

echo Python was not found on this computer.
echo Please install Python 3 first, then run this file again.
pause

:end
endlocal
