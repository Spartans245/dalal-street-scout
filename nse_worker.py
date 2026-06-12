"""
nse_worker.py — NSE Universe + Fundamentals Worker
===================================================
Runs as a standalone process, called by orchestrator.py.

Modes:
  python nse_worker.py --universe       # Download ticker universe from NSE
  python nse_worker.py --fundamentals   # Fetch PE, MCap, sector, name per stock
  python nse_worker.py --test           # Test mode: 5 tickers, fundamentals only

Output:
  data/raw/nse.json      — universe + fundamentals per stock
  data/status/nse.json   — exit status, count, duration, errors

Exit codes:
  0 = success
  1 = fatal error (orchestrator should abort)
"""

import sys, os, json, time, math, datetime, argparse
try:
    from curl_cffi import requests
except ImportError:
    import requests  # fallback — may not bypass Akamai WAF

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

BASE_DIR            = os.path.dirname(os.path.abspath(__file__))
RAW_DIR             = os.path.join(BASE_DIR, 'data', 'raw')
STATUS_DIR          = os.path.join(BASE_DIR, 'data', 'status')
RAW_FILE            = os.path.join(RAW_DIR,    'nse.json')
STATUS_FILE         = os.path.join(STATUS_DIR, 'nse.json')
UNIVERSE_STATUS_FILE= os.path.join(STATUS_DIR, 'nse_universe.json')

os.makedirs(RAW_DIR,    exist_ok=True)
os.makedirs(STATUS_DIR, exist_ok=True)

QUOTE_DELAY  = 1.2    # seconds between NSE quote-equity calls (raised to avoid Akamai rate ban)
SESSION_TTL  = 300    # renew NSE session every 5 min

NSE_HEADERS = {
    'Accept':          'application/json, text/plain, */*',
    'Accept-Language': 'en-IN,en-US;q=0.9,en;q=0.8',
    'Referer':         'https://www.nseindia.com/',
}

# ════════════════════════════════════════════════════════════════════
# STATUS WRITER
# ════════════════════════════════════════════════════════════════════
def write_status(status, count=0, errors=0, duration=0, message='', universe=False):
    data = {
        'status':    status,
        'count':     count,
        'errors':    errors,
        'duration':  round(duration, 1),
        'message':   message,
        'saved_at':  datetime.datetime.now().isoformat(),
    }
    path = UNIVERSE_STATUS_FILE if universe else STATUS_FILE
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)


# ════════════════════════════════════════════════════════════════════
# NSE SESSION
# ════════════════════════════════════════════════════════════════════
class NSESession:
    def __init__(self):
        self.session   = None
        self.last_init = 0

    def get(self, url, **kwargs):
        if self.session is None or (time.time() - self.last_init) > SESSION_TTL:
            self._init()
        return self.session.get(url, headers=NSE_HEADERS, timeout=12, **kwargs)

    def _init(self):
        # curl_cffi impersonates Chrome's TLS fingerprint — bypasses Akamai bot detection.
        # Without this, Akamai returns 403 for all /api/ paths even on residential IPs.
        try:
            s = requests.Session(impersonate='chrome124')
        except TypeError:
            # Fallback if plain requests was imported instead of curl_cffi
            s = requests.Session()
        try:
            s.get('https://www.nseindia.com', headers=NSE_HEADERS, timeout=15)
            time.sleep(1.5)
            # Warmup: browse a real page to activate the _abck Akamai cookie
            s.get('https://www.nseindia.com/market-data/live-equity-market',
                  headers={**NSE_HEADERS, 'Referer': 'https://www.nseindia.com/'}, timeout=12)
            time.sleep(1.0)
        except Exception:
            pass
        self.session   = s
        self.last_init = time.time()
        print('[NSE] Session initialised')


nse = NSESession()


# ════════════════════════════════════════════════════════════════════
# UNIVERSE — EQUITY_L.csv + SME Emerge
# ════════════════════════════════════════════════════════════════════
def fetch_universe():
    """Download ticker universe from NSE. Returns list of dicts {symbol, board}."""
    symbols = {}

    # Main board — EQUITY_L.csv
    print('[NSE] Fetching EQUITY_L.csv (main board)...')
    try:
        r = nse.get('https://archives.nseindia.com/content/equities/EQUITY_L.csv')
        r.raise_for_status()
        lines = r.text.strip().split('\n')
        for line in lines[1:]:
            parts = line.split(',')
            if parts and parts[0].strip().strip('"'):
                sym = parts[0].strip().strip('"')
                symbols[sym] = 'main'
        print(f'[NSE] Main board: {len(symbols)} symbols from EQUITY_L.csv')
    except Exception as e:
        print(f'[NSE] ERROR fetching EQUITY_L.csv: {e}')
        return None

    # SME Emerge — NIFTY SME EMERGE index
    print('[NSE] Fetching SME Emerge list...')
    try:
        time.sleep(0.5)
        r = nse.get('https://www.nseindia.com/api/equity-stockIndices?index=NIFTY%20SME%20EMERGE')
        r.raise_for_status()
        data = r.json()
        stocks = data.get('data', [])
        sme_count = 0
        for s in stocks:
            sym = s.get('symbol', '')
            if sym and sym != 'NIFTY SME EMERGE':
                symbols[sym] = 'sme'
                sme_count += 1
        print(f'[NSE] SME Emerge: {sme_count} symbols')
    except Exception as e:
        print(f'[NSE] WARN: SME Emerge fetch failed: {e} — continuing with main board only')

    result = [{'symbol': sym, 'board': board} for sym, board in symbols.items()]
    print(f'[NSE] Total universe: {len(result)} symbols')
    return result


# ════════════════════════════════════════════════════════════════════
# FUNDAMENTALS — quote-equity per stock
# ════════════════════════════════════════════════════════════════════
def fetch_quote(symbol):
    """Fetch PE, MCap, sector, name, 52W High/Low for one stock from NSE."""
    try:
        r = nse.get(f'https://www.nseindia.com/api/quote-equity?symbol={symbol}')
        if r.status_code != 200:
            return None
        data = r.json()

        price_info = data.get('priceInfo', {})
        info_block = data.get('info', {})
        meta       = data.get('metadata', {})

        if info_block.get('isSuspended'):
            return None

        last_price = float(price_info.get('lastPrice') or 0)
        prev_close = float(price_info.get('previousClose') or last_price)
        whl        = price_info.get('weekHighLow', {})
        wk52h      = float(whl.get('max') or 0)
        wk52l      = float(whl.get('min') or 0)

        pe_raw = meta.get('pdSymbolPe') or 0
        try:    pe = round(float(pe_raw), 1)
        except: pe = 0.0
        if math.isnan(pe): pe = 0.0

        name   = info_block.get('companyName') or symbol
        sector = meta.get('industry') or info_block.get('industry') or 'Others'

        # MCap from trade_info
        mcap_cr = 0
        try:
            time.sleep(0.2)
            r2 = nse.get(f'https://www.nseindia.com/api/quote-equity?symbol={symbol}&section=trade_info')
            d2 = r2.json()
            ti = d2.get('marketDeptOrderBook', {}).get('tradeInfo', {})
            raw = ti.get('totalMarketCap') or 0
            if raw:
                mcap_cr = int(round(float(raw), 0))
        except Exception:
            pass

        return {
            'symbol':   symbol,
            'name':     name,
            'sector':   sector,
            'pe':       pe,
            'mcap':     mcap_cr,
            'wk52High': round(wk52h, 2),
            'wk52Low':  round(wk52l, 2),
            'price':    round(last_price, 2),
            'prevClose':round(prev_close, 2),
        }
    except Exception as e:
        print(f'[NSE] quote {symbol}: {e}')
        return None


def fetch_fundamentals(universe, test_symbols=None):
    """Fetch fundamentals for all symbols in universe. Returns list of dicts."""
    symbols = test_symbols or [u['symbol'] for u in universe]
    board_map = {u['symbol']: u.get('board', 'main') for u in universe}

    results = []
    errors  = 0
    total   = len(symbols)

    for i, sym in enumerate(symbols, 1):
        if i % 100 == 0 or i == 1:
            print(f'[NSE] Fundamentals {i}/{total} ({errors} errors)...')
        q = fetch_quote(sym)
        if q:
            q['board'] = board_map.get(sym, 'main')
            results.append(q)
        else:
            errors += 1
        time.sleep(QUOTE_DELAY)

    return results, errors


# ════════════════════════════════════════════════════════════════════
# LOAD / MERGE existing raw file
# ════════════════════════════════════════════════════════════════════
def load_existing():
    """Load existing data/raw/nse.json if present."""
    if not os.path.exists(RAW_FILE):
        return {'universe': [], 'fundamentals': {}}
    try:
        with open(RAW_FILE) as f:
            return json.load(f)
    except Exception:
        return {'universe': [], 'fundamentals': {}}


def save_raw(data):
    with open(RAW_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False)
    print(f'[NSE] Saved {RAW_FILE}')


# ════════════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--universe',     action='store_true', help='Download ticker universe')
    parser.add_argument('--fundamentals', action='store_true', help='Fetch fundamentals for all stocks')
    parser.add_argument('--test',         action='store_true', help='Test mode: 5 tickers only')
    args = parser.parse_args()

    if not any([args.universe, args.fundamentals, args.test]):
        print('Usage: python nse_worker.py --universe | --fundamentals | --test')
        sys.exit(1)

    t0 = time.time()
    write_status('running', message='Starting...')
    write_status('running', message='Starting...', universe=True)

    existing = load_existing()

    # ── UNIVERSE MODE ─────────────────────────────────────────────
    if args.universe:
        write_status('running', message='Fetching universe from NSE...', universe=True)
        universe = fetch_universe()
        if universe is None:
            write_status('error', message='Failed to fetch EQUITY_L.csv', universe=True)
            sys.exit(1)
        existing['universe'] = universe
        existing['universe_saved_at'] = datetime.datetime.now().isoformat()
        save_raw(existing)
        duration = time.time() - t0
        sme_count = sum(1 for u in universe if u.get('board') == 'sme')
        main_count = len(universe) - sme_count
        write_status('done', count=len(universe), duration=duration,
                     message=f'Universe saved: {main_count} main + {sme_count} SME = {len(universe)} total',
                     universe=True)
        print(f'[NSE] Universe done: {main_count} main + {sme_count} SME in {duration:.0f}s')
        sys.exit(0)

    # ── FUNDAMENTALS MODE ─────────────────────────────────────────
    if args.fundamentals or args.test:
        universe = existing.get('universe', [])
        if not universe:
            # Try fetching universe on the fly if missing
            print('[NSE] No universe found — fetching now...')
            universe = fetch_universe()
            if not universe:
                write_status('error', message='No universe available')
                sys.exit(1)
            existing['universe'] = universe

        test_symbols = ['RELIANCE', 'TCS', 'INFY', 'HDFCBANK', 'WIPRO'] if args.test else None
        write_status('running', message=f'Fetching fundamentals for {len(test_symbols or universe)} stocks...')

        results, errors = fetch_fundamentals(universe, test_symbols)

        # Store as dict keyed by symbol for easy lookup by compute.py
        fund_map = {r['symbol']: r for r in results}
        # In test mode, only update the fundamentals for the tested symbols — never wipe production data
        # In full mode, only overwrite if we got a meaningful number of results (>10% of universe)
        # to protect against a silent total-failure wiping good cached data.
        min_acceptable = max(10, len(universe) // 10)
        if args.test:
            existing['fundamentals'].update(fund_map)
        elif len(results) >= min_acceptable:
            existing['fundamentals'] = fund_map
        else:
            print(f'[NSE] WARN: only {len(results)}/{len(universe)} stocks fetched — keeping existing fundamentals cache')
        existing['fundamentals_saved_at'] = datetime.datetime.now().isoformat()
        save_raw(existing)

        duration = time.time() - t0
        write_status('done', count=len(results), errors=errors, duration=duration,
                     message=f'Fundamentals saved: {len(results)} stocks, {errors} errors')
        write_status('done', count=len(universe), duration=duration,
                     message=f'Universe: {len(universe)} stocks', universe=True)
        print(f'[NSE] Fundamentals done: {len(results)} stocks, {errors} errors in {duration:.0f}s')
        sys.exit(0 if errors < len(results) * 0.2 else 1)  # fail if >20% errors


if __name__ == '__main__':
    main()
