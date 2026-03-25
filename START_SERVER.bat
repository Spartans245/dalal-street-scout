@echo off
title Dalal Street Scout — Server
cd /d d:\Dalal_street

echo.
echo  ============================================
echo   DALAL STREET SCOUT — Starting Server
echo  ============================================
echo.

:: Check if server already running on port 5000
netstat -an | findstr ":5000" | findstr "LISTENING" > nul
if %errorlevel%==0 (
    echo  Server is already running on port 5000.
    echo  Opening browser...
    echo.
    start http://localhost:5000
    timeout /t 3 /nobreak >nul
    exit /b 0
)

echo  Starting server at http://localhost:5000
echo  Opening browser...
echo.
timeout /t 2 /nobreak >nul
start http://localhost:5000

rem ── Set your Anthropic API key here ──────────────────────────────────────
rem set ANTHROPIC_API_KEY=sk-ant-your-key-here
rem ─────────────────────────────────────────────────────────────────────────

set PYTHON="C:\Users\RAJARSHI\AppData\Local\Python\pythoncore-3.14-64\python.exe"
set CRASHES=0

:restart
echo  [%time%] Server starting...
%PYTHON% -W ignore server.py
set EXIT_CODE=%errorlevel%
echo.

:: Exit code 0 = clean exit (e.g. port already in use detected by Python)
if %EXIT_CODE%==0 (
    echo  Server exited cleanly.
    exit /b 0
)

set /a CRASHES+=1
if %CRASHES% geq 5 (
  echo  ============================================
  echo   Server crashed 5 times in a row.
  echo   Check the error output above.
  echo   Fix the issue then restart manually.
  echo  ============================================
  pause
  exit /b 1
)
echo  [%time%] Server stopped. Restarting in 10 seconds... [attempt %CRASHES% of 5]
echo  (Close this window to stop permanently)
timeout /t 10 /nobreak
goto restart
