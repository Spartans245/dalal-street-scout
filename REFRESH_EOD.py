"""
REFRESH_EOD.py — EOD pipeline launcher
Called by Task Scheduler at 3:35 PM Mon–Fri (target execution: ~4:05 PM after 30-min wait).
Equivalent to REFRESH_EOD.bat but runnable directly by Python.
"""
import sys
import os
import time
import datetime
import subprocess
import warnings

warnings.filterwarnings('ignore')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON = sys.executable  # same Python that launched this script


def is_nse_holiday(today_str):
    """Return True if today is an NSE trading holiday. Falls back to False on error."""
    try:
        import requests
        s = requests.Session()
        s.get('https://www.nseindia.com', headers={'User-Agent': 'Mozilla/5.0'}, timeout=8)
        time.sleep(0.3)
        r = s.get(
            'https://www.nseindia.com/api/holiday-master?type=trading',
            headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json',
                     'Referer': 'https://www.nseindia.com'},
            timeout=8,
        )
        holidays = [h['tradingDate'] for h in r.json().get('CM', [])]
        return today_str in holidays
    except Exception as e:
        print(f'[EOD] Holiday check failed ({e}) — proceeding anyway.')
        return False


def run(script, args=None):
    cmd = [PYTHON, '-W', 'ignore', os.path.join(BASE_DIR, script)] + (args or [])
    print(f'[EOD] Running: {" ".join(cmd)}')
    result = subprocess.run(cmd, cwd=BASE_DIR)
    return result.returncode


def main():
    # Wait 30 minutes for closing auction to settle (target: ~4:05 PM)
    print('[EOD] Waiting 30 minutes for closing auction to settle...')
    time.sleep(1800)

    today = datetime.date.today()
    today_fmt = today.strftime('%d-%b-%Y')  # NSE format e.g. "07-Apr-2026"

    # Skip weekends
    if today.weekday() >= 5:
        print(f'[EOD] {today_fmt} is a weekend — skipping pipeline.')
        sys.exit(0)

    # Skip NSE holidays
    if is_nse_holiday(today_fmt):
        print(f'[EOD] NSE holiday today ({today_fmt}) — skipping pipeline.')
        sys.exit(0)

    print(f'[EOD] {today_fmt} is a trading day — proceeding.')

    # EOD pipeline
    code = run('orchestrator.py', ['--eod'])
    if code != 0:
        print(f'[EOD] orchestrator.py --eod exited with code {code}')

    code2 = run('regression_worker.py')
    if code2 != 0:
        print(f'[EOD] regression_worker.py exited with code {code2}')

    sys.exit(0)


if __name__ == '__main__':
    main()
