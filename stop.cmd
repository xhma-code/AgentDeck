@echo off
@chcp 65001 >nul
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
echo   Stopping AgentDeck (ports: %PORTS%)
echo.

rem 1) Stop AgentDeck python processes belonging strictly to this checkout
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$repoPath = [regex]::Escape((Resolve-Path '%~dp0').Path.TrimEnd('\/')); " ^
  "Get-CimInstance Win32_Process | Where-Object { ($_.Name -match '^python' -or $_.Name -match '^uvicorn') -and $_.CommandLine -and ($_.CommandLine -match $repoPath) } | ForEach-Object { Write-Host ('  [x] ' + $_.Name + '  PID ' + $_.ProcessId); Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"

rem 2) Check listeners on target ports - only terminate if belonging to this checkout
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$repoPath = [regex]::Escape((Resolve-Path '%~dp0').Path.TrimEnd('\/')); " ^
  "Get-NetTCPConnection -LocalPort %PORTS% -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object { " ^
  "  if ($_ -gt 0) { " ^
  "    $p = Get-CimInstance Win32_Process -Filter ('ProcessId = ' + $_) -ErrorAction SilentlyContinue; " ^
  "    if ($p -and $p.CommandLine -and ($p.CommandLine -match $repoPath)) { " ^
  "      Write-Host ('  [x] port ' + %PORTS% + ' (AgentDeck PID ' + $_ + ')'); " ^
  "      Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue; " ^
  "    } elseif ($p) { " ^
  "      Write-Host ('  [!] port ' + %PORTS% + ' is occupied by unrelated process (' + $p.Name + ' PID ' + $_ + '), skipping.'); " ^
  "    } " ^
  "  } " ^
  "}"

timeout /t 1 /nobreak >nul 2>nul
echo.
echo   AgentDeck stopped and port(s) freed.
echo.
if "%NO_PAUSE%"=="0" pause
