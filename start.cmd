@echo off
setlocal
title AgentDeck
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo [x] Python not found in PATH. Please install Python 3.10+ first.
  pause
  exit /b 1
)

echo [*] 正在检查并清理已存在的 AgentDeck 进程...
call "%~dp0stop.cmd" --no-pause
title AgentDeck

echo [*] 正在启动 AgentDeck (热重载模式)...
echo.

python run.py --reload %*
set "CODE=%ERRORLEVEL%"

if not "%CODE%"=="0" (
  echo.
  echo [x] AgentDeck exited with code %CODE%
  pause
)
