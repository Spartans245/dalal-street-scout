@echo off
title Dalal Street Scout — Server
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
python -W ignore server.py
pause
