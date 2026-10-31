@echo off
chcp 65001 >nul
setlocal
title OpenDev Elite 一键启动
cd /d "%~dp0"

echo.
echo 正在启动 OpenDev Elite ...
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\one-click-start.ps1"
set "ERR=%ERRORLEVEL%"

echo.
if not "%ERR%"=="0" (
  echo 启动失败，错误码 %ERR%。请查看上方信息。
  pause
  exit /b %ERR%
)

echo 窗口可关闭；服务在后台继续运行。
pause
endlocal
