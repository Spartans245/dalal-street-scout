@echo off
cd /d "%~dp0"

:: Wait for closing auction to fully settle in Kite API (~4:05 PM target)
:: Task Scheduler triggers at 3:35 PM; 30-min delay brings execution to ~4:05 PM
echo [EOD] Waiting 30 minutes for closing auction to settle...
timeout /t 1800 /nobreak >nul

:: Check if today is an NSE trading holiday before running the pipeline
"C:\Users\RAJARSHI\AppData\Local\Python\pythoncore-3.14-64\python.exe" -W ignore -c "
import sys, datetime, time
try:
    import requests
    s = requests.Session()
    s.get('https://www.nseindia.com', headers={'User-Agent':'Mozilla/5.0'}, timeout=8)
    time.sleep(0.3)
    r = s.get('https://www.nseindia.com/api/holiday-master?type=trading',
              headers={'User-Agent':'Mozilla/5.0','Accept':'application/json','Referer':'https://www.nseindia.com'},
              timeout=8)
    holidays = [h['tradingDate'] for h in r.json().get('CM', [])]
    today = datetime.date.today().strftime('%d-%b-%Y')
    if today in holidays:
        print(f'[EOD] NSE holiday today ({today}) — skipping pipeline.')
        sys.exit(1)
    print(f'[EOD] {today} is a trading day — proceeding.')
    sys.exit(0)
except Exception as e:
    print(f'[EOD] Holiday check failed ({e}) — proceeding anyway.')
    sys.exit(0)
"
if %errorlevel%==1 goto :eof

:: EOD pipeline: re-scan OHLCV from Kite + recompute + EVALS update + model
"C:\Users\RAJARSHI\AppData\Local\Python\pythoncore-3.14-64\python.exe" -W ignore orchestrator.py --eod
"C:\Users\RAJARSHI\AppData\Local\Python\pythoncore-3.14-64\python.exe" -W ignore regression_worker.py
