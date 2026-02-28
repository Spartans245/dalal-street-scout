@echo off
title Dalal Street Scout — Server
cd /d d:\Dalal_street

echo.
echo  ============================================
echo   DALAL STREET SCOUT — Starting Server
echo  ============================================
echo.
echo  Installing required libraries...
pip install yfinance pandas requests -q
echo.
echo  Starting server at http://localhost:5000
echo  Opening browser...
echo.
timeout /t 2 /nobreak >nul
start http://localhost:5000

:restart
echo  [%time%] Server starting...
python -W ignore server.py
echo.
echo  [%time%] Server stopped. Restarting in 10 seconds...
echo  (Close this window to stop)
timeout /t 10 /nobreak
goto restart
