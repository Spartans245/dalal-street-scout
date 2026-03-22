"""
yf_worker.py — Yahoo Finance Supplemental Fundamentals Worker
=============================================================
Runs as a standalone process, called by orchestrator.py.

Provides data unavailable from Kite or NSE:
  - D/E Ratio (debtToEquity)
  - ROE (returnOnEquity)

Modes:
  python yf_worker.py --fundamentals  # Fetch D/E and ROE for all NSE stocks
  python yf_worker.py --test          # Test mode: 5 tickers only

Output:
  data/raw/yf.json     — {symbol: {debtEq, roe}} dict
  data/status/yf.json  — exit status, count, duration, errors

Exit codes:
  0 = success
  1 = fatal error (orchestrator should abort)

Universe source of truth:
  Reads data/raw/nse.json to get the symbol list.
  Yahoo Finance tickers: SYMBOL.NS format.
"""

import sys, os, json, time, datetime, argparse, math

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

import warnings
warnings.filterwarnings('ignore')

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
RAW_DIR     = os.path.join(BASE_DIR, 'data', 'raw')
STATUS_DIR  = os.path.join(BASE_DIR, 'data', 'status')
RAW_FILE    = os.path.join(RAW_DIR,    'yf.json')
NSE_FILE    = os.path.join(RAW_DIR,    'nse.json')
STATUS_FILE = os.path.join(STATUS_DIR, 'yf.json')

os.makedirs(RAW_DIR,    exist_ok=True)
os.makedirs(STATUS_DIR, exist_ok=True)

# ── Config ─────────────────────────────────────────────────────────────
INFO_DELAY    = 1.5   # seconds between yfinance .info calls
RETRY_DELAYS  = [5, 15, 45]  # backoff on 429: wait 5s, 15s, 45s before giving up

try:
    import yfinance as yf
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'yfinance', '-q'])
    import yfinance as yf

# yfinance 1.2.0+ manages its own session — no custom session needed


# ════════════════════════════════════════════════════════════════════
# STATUS WRITER
# ════════════════════════════════════════════════════════════════════
def write_status(status, count=0, errors=0, duration=0, message=''):
    data = {
        'status':   status,
        'count':    count,
        'errors':   errors,
        'duration': round(duration, 1),
        'message':  message,
        'saved_at': datetime.datetime.now().isoformat(),
    }
    with open(STATUS_FILE, 'w') as f:
        json.dump(data, f, indent=2)


# ════════════════════════════════════════════════════════════════════
# NSE UNIVERSE
# ════════════════════════════════════════════════════════════════════
def load_universe():
    if not os.path.exists(NSE_FILE):
        return None
    try:
        with open(NSE_FILE) as f:
            data = json.load(f)
        return data.get('universe', [])
    except Exception as e:
        print(f'[YF] Failed to load NSE universe: {e}')
        return None


# ════════════════════════════════════════════════════════════════════
# FETCH ONE — D/E and ROE from Yahoo Finance
# ════════════════════════════════════════════════════════════════════
def fetch_yf_fundamentals(symbol):
    """Fetch D/E ratio and ROE for one NSE stock.
    Returns {debtEq, roe} or None on failure.
    Retries with backoff on 429 Too Many Requests.
    """
    for attempt, wait in enumerate([0] + RETRY_DELAYS):
        if wait:
            time.sleep(wait)
        try:
            ticker = yf.Ticker(f'{symbol}.NS')
            info   = ticker.info
            if not info:
                return None

            # D/E Ratio: yfinance returns as percentage (e.g. 45.3 = 0.453 D/E)
            de_raw  = info.get('debtToEquity')
            de_val  = None
            if de_raw is not None and not math.isnan(float(de_raw)):
                de_val = round(float(de_raw) / 100.0, 2)  # convert % → ratio

            # ROE: returned as decimal (e.g. 0.23 = 23%)
            roe_raw = info.get('returnOnEquity')
            roe_val = None
            if roe_raw is not None and not math.isnan(float(roe_raw)):
                roe_val = round(float(roe_raw) * 100.0, 1)  # convert to %

            if de_val is None and roe_val is None:
                return None  # nothing useful

            return {'debtEq': de_val, 'roe': roe_val}

        except Exception as e:
            err = str(e)
            if '429' in err or 'Too Many Requests' in err:
                if attempt < len(RETRY_DELAYS):
                    continue  # retry after backoff
            return None  # other errors or exhausted retries
    return None


# ════════════════════════════════════════════════════════════════════
# MAIN FETCH LOOP
# ════════════════════════════════════════════════════════════════════
def fetch_fundamentals(universe, test_symbols=None):
    """Fetch D/E + ROE for all symbols. Returns (fund_map, errors)."""
    symbols = test_symbols or [u['symbol'] for u in universe]
    total   = len(symbols)
    fund    = {}
    errors  = 0

    for i, sym in enumerate(symbols, 1):
        if i % 100 == 0 or i == 1:
            print(f'[YF] Fundamentals {i}/{total} ({errors} errors)...')
        result = fetch_yf_fundamentals(sym)
        if result:
            fund[sym] = result
        else:
            errors += 1
        time.sleep(INFO_DELAY)

    return fund, errors


# ════════════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--fundamentals', action='store_true', help='Fetch D/E and ROE for all stocks')
    parser.add_argument('--test',         action='store_true', help='Test mode: 5 tickers only')
    args = parser.parse_args()

    if not any([args.fundamentals, args.test]):
        print('Usage: python yf_worker.py --fundamentals | --test')
        sys.exit(1)

    t0 = time.time()
    write_status('running', message='Starting...')

    universe = load_universe()
    if universe is None:
        msg = 'NSE universe not found — run nse_worker.py --universe first'
        print(f'[YF] {msg}')
        write_status('error', message=msg)
        sys.exit(1)

    test_symbols = ['RELIANCE', 'TCS', 'INFY', 'HDFCBANK', 'WIPRO'] if args.test else None
    target_count = len(test_symbols or universe)

    write_status('running', message=f'Fetching D/E and ROE for {target_count} stocks...')
    print(f'[YF] Fetching fundamentals for {target_count} stocks...')

    fund_map, errors = fetch_fundamentals(universe, test_symbols)

    # Load existing to preserve previous data for unchanged symbols
    existing_fund = {}
    if os.path.exists(RAW_FILE):
        try:
            with open(RAW_FILE) as f:
                old = json.load(f)
            existing_fund = old.get('fundamentals', {})
        except Exception:
            pass

    # Merge: new results override, existing kept for failed
    merged = {**existing_fund, **fund_map}

    duration = time.time() - t0
    output = {
        'saved_at':     datetime.datetime.now().isoformat(),
        'count':        len(fund_map),
        'errors':       errors,
        'fundamentals': merged,
    }

    try:
        with open(RAW_FILE, 'w', encoding='utf-8') as f:
            json.dump(output, f, ensure_ascii=False)
        print(f'[YF] Saved {len(fund_map)} records → {RAW_FILE}')
    except Exception as e:
        msg = f'Failed to write {RAW_FILE}: {e}'
        print(f'[YF] FATAL: {msg}')
        write_status('error', duration=duration, message=msg)
        sys.exit(1)

    write_status(
        'done',
        count=len(fund_map),
        errors=errors,
        duration=duration,
        message=f'Saved: {len(fund_map)} stocks with D/E or ROE, {errors} errors',
    )
    print(f'[YF] Done: {len(fund_map)} stocks in {duration:.0f}s ({errors} errors)')
    # D/E and ROE are supplemental — high error rate is tolerable (many stocks on YF lack data)
    sys.exit(0)


if __name__ == '__main__':
    main()
