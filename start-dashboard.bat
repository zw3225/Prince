@echo off
setlocal
chcp 65001 >nul

cd /d "%~dp0"

if "%TREND_RADAR_HOST%"=="" set TREND_RADAR_HOST=127.0.0.1
if "%TREND_RADAR_PORT%"=="" set TREND_RADAR_PORT=8787
set DASHBOARD_URL=http://127.0.0.1:%TREND_RADAR_PORT%/

echo 海外趋势爆款雷达正在启动...
echo.
echo 浏览器会自动打开这个地址：
echo %DASHBOARD_URL%
echo.
echo 使用看板时请不要关闭这个黑色窗口。
echo.

start "" powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 2; Start-Process '%DASHBOARD_URL%'"

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

echo 没有找到 Python。
echo 请先安装 Python 3.10 或以上版本，安装时勾选 Add Python to PATH。
pause
goto :end

:server_stopped
echo.
echo 看板服务已停止，或启动失败。
echo 如果浏览器显示“拒绝连接”，请确认上面有没有报错，并重新双击本文件。
echo 正确地址是：%DASHBOARD_URL%
pause

:end
endlocal
