@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo   医学影像阅片与手术规划系统 - MVP
echo   ---------------------------------
echo.
node start.mjs %*
if errorlevel 1 (
  echo.
  echo   启动失败，请检查上方错误信息。
  pause
)
