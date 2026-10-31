@echo off
@chcp 65001 >nul
setlocal
title AgentDeck
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo [x] Python not found in PATH. Please install Python 3.10+ first.
  pause
  exit /b 1
)

echo [*] Checking and terminating previous AgentDeck instances...
call "%~dp0stop.cmd" --no-pause
title AgentDeck

echo [*] Starting AgentDeck (hot reload enabled)...
echo.

python run.py %*
set "CODE=%ERRORLEVEL%"

if not "%CODE%"=="0" (
  echo.
  echo [x] AgentDeck exited with code %CODE%
  pause
)
