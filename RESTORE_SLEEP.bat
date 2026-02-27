@echo off
:: Dalal Street Scout — Restore Sleep Script
:: Triggered by Windows Task Scheduler at 4:00 PM Mon-Fri
:: Market is closed — restore normal power settings

:: Restore AC sleep timeout (30 min)
powercfg /change standby-timeout-ac 30
powercfg /change monitor-timeout-ac 10

echo [%date% %time%] Market closed — sleep settings restored >> d:\Dalal_street\market_start.log
