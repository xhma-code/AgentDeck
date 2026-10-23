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

python run.py %*
set "CODE=%ERRORLEVEL%"

if not "%CODE%"=="0" (
  echo.
  echo [x] AgentDeck exited with code %CODE%
  pause
)
