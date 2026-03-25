@echo off
cd /d "%~dp0"
:: EOD pipeline: re-scan OHLCV from Kite + recompute + EVALS update
"C:\Users\RAJARSHI\AppData\Local\Python\pythoncore-3.14-64\python.exe" -W ignore orchestrator.py --from kite --eod
