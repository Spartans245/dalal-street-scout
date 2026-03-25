@echo off
cd /d d:\Dalal_street

:: Check if server already running on port 5000
netstat -an | findstr ":5000" | findstr "LISTENING" > nul
if %errorlevel%==0 (
    echo Server already running on port 5000 — skipping.
    exit /b 0
)

start "Dalal Street Scout" /min "C:\Users\RAJARSHI\AppData\Local\Python\pythoncore-3.14-64\python.exe" -W ignore server.py
