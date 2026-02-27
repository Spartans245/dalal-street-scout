@echo off
:: Dalal Street Scout — Auto Start Script
:: Triggered by Windows Task Scheduler at 7:30 AM Mon-Fri
:: Wakes PC, prevents sleep, starts server if not already running

cd /d d:\Dalal_street

:: Disable sleep during market hours (restored at 4 PM by RESTORE_SLEEP.bat)
powercfg /change standby-timeout-ac 0

:: Log startup attempt
echo [%date% %time%] Market start triggered >> d:\Dalal_street\market_start.log

:: Check if server already running on port 5000
netstat -an | findstr ":5000" | findstr "LISTENING" > nul
if %errorlevel%==0 (
    echo [%date% %time%] Server already running — skipping >> d:\Dalal_street\market_start.log
    exit /b 0
)

:: Start server in minimized window
echo [%date% %time%] Starting Dalal Street Scout... >> d:\Dalal_street\market_start.log
start "Dalal Street Scout" /min python d:\Dalal_street\server.py
echo [%date% %time%] Server started >> d:\Dalal_street\market_start.log
