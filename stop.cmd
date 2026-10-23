@echo off
setlocal enabledelayedexpansion
title AgentDeck - Stop
cd /d "%~dp0"

set "PORTS=8765"
set "NO_PAUSE=0"

if /i "%~1"=="--no-pause" (
  set "NO_PAUSE=1"
) else if not "%~1"=="" (
  set "PORTS=%~1"
)
if /i "%~2"=="--no-pause" set "NO_PAUSE=1"

echo.
echo   Stopping AgentDeck ^(ports: %PORTS%^)
echo.

rem 1) Stop AgentDeck python supervisor process
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^python' -and $_.CommandLine -match 'run\.py' } | ForEach-Object { Write-Host ('  [x] ' + $_.Name + '  PID ' + $_.ProcessId); Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"

rem 2) Kill anything still listening on target ports
set "TOTAL=0"
for %%P in (%PORTS%) do (
  set "HIT=0"
  for /f "tokens=5" %%I in ('netstat -ano ^| findstr /c:":%%P " ^| findstr /c:"LISTENING"') do (
    if not "%%I"=="0" (
      taskkill /PID %%I /T /F >nul 2>nul
      if not errorlevel 1 (
        echo   [x] port %%P  PID %%I
        set "HIT=1"
      )
    )
  )
  if "!HIT!"=="0" echo   [ ] port %%P  not listening
  set /a TOTAL+=HIT
)

echo.
if "%TOTAL%"=="0" (
  echo   Nothing was listening. AgentDeck looks stopped.
) else (
  echo   AgentDeck stopped ^(%TOTAL% port^(s^) freed^).
)
echo.
if "%NO_PAUSE%"=="0" pause
