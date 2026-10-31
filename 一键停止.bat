@echo off
chcp 65001 >nul
setlocal
title OpenDev Elite 一键停止
cd /d "%~dp0"

echo.
echo 正在停止 OpenDev Elite ...
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\one-click-stop.ps1"
set "ERR=%ERRORLEVEL%"

echo.
if not "%ERR%"=="0" (
  echo 停止失败，错误码 %ERR%。请查看上方信息。
  pause
  exit /b %ERR%
)

pause
endlocal
