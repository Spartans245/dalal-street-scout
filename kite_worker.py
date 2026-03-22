"""
kite_worker.py — Kite Connect OHLCV Worker
===========================================
Runs as a standalone process, called by orchestrator.py.

Modes:
  python kite_worker.py --scan           # Full OHLCV scan  → data/raw/kite.json
  python kite_worker.py --price-refresh  # Live price patch → data/computed/stocks.json
  python kite_worker.py --test           # Test: 5 tickers, scan only

Output:
  data/raw/kite.json        — raw OHLCV rows per stock (compute.py processes this)
  data/computed/stocks.json — patched in-place during price refresh (price/change/upsidePct)
  data/status/kite.json     — exit status, count, duration, errors

Exit codes:
  0 = success
  1 = fatal error (orchestrator should abort pipeline)
  2 = auth required (orchestrator should notify and halt)

Universe source of truth:
  Reads data/raw/nse.json to get the symbol list (NSE owns the universe).
  Symbols with no Kite instrument token are skipped (logged as warnings).
"""

import sys, os, json, time, math, datetime, argparse

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

import warnings
warnings.filterwarnings('ignore')

BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
RAW_DIR       = os.path.join(BASE_DIR, 'data', 'raw')
COMPUTED_DIR  = os.path.join(BASE_DIR, 'data', 'computed')
STATUS_DIR    = os.path.join(BASE_DIR, 'data', 'status')
KITE_DIR      = os.path.join(BASE_DIR, 'kite')

RAW_FILE      = os.path.join(RAW_DIR,     'kite.json')
NSE_FILE      = os.path.join(RAW_DIR,     'nse.json')
COMPUTED_FILE = os.path.join(COMPUTED_DIR,'stocks.json')
STATUS_FILE   = os.path.join(STATUS_DIR,  'kite.json')
INSTRUMENTS_FILE = os.path.join(KITE_DIR, 'instruments.json')
TOKEN_FILE    = os.path.join(BASE_DIR, 'kite_token.json')
CONFIG_FILE   = os.path.join(BASE_DIR, 'kite_config.json')

for d in [RAW_DIR, COMPUTED_DIR, STATUS_DIR, KITE_DIR]:
    os.makedirs(d, exist_ok=True)

# ── Config ─────────────────────────────────────────────────────────────
HIST_DELAY       = 0.35   # seconds between historical_data() calls
HISTORY_DAYS     = 280    # calendar days to fetch (yields ~193 trading days)
INSTRUMENTS_TTL  = 86400  # refresh instrument map once per day (seconds)
LTP_BATCH        = 500    # Kite max instruments per ltp() call

# ── Shared path ────────────────────────────────────────────────────────
sys.path.insert(0, BASE_DIR)

try:
    import pandas as pd
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'pandas', '-q'])
    import pandas as pd


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
# TIME
# ════════════════════════════════════════════════════════════════════
def get_ist():
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5, minutes=30)))


def is_market_hours():
    """True during NSE trading hours 09:15–15:30 IST Mon–Fri."""
    ist = get_ist()
    if ist.weekday() >= 5:
        return False
    t = ist.time()
    return datetime.time(9, 15) <= t <= datetime.time(15, 30)


# ════════════════════════════════════════════════════════════════════
# KITE AUTH
# ════════════════════════════════════════════════════════════════════
def load_kite():
    """Return authenticated KiteConnect instance. Raises RuntimeError on failure."""
    from kiteconnect import KiteConnect

    if not os.path.exists(TOKEN_FILE):
        raise RuntimeError(f'{TOKEN_FILE} not found — run kite_auth.py first')
    if not os.path.exists(CONFIG_FILE):
        raise RuntimeError(f'{CONFIG_FILE} not found')

    with open(TOKEN_FILE) as f:
        tok = json.load(f)
    with open(CONFIG_FILE) as f:
        cfg = json.load(f)

    access_token = tok.get('access_token', '')
    token_date   = tok.get('date', '')
    api_key      = cfg.get('api_key', '').strip()

    today = get_ist().date().isoformat()
    if not access_token or token_date != today:
        raise RuntimeError(
            f'Kite token is stale (token date={token_date}, today={today}). '
            'Run: python kite_auth.py'
        )

    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(access_token)
    return kite


# ════════════════════════════════════════════════════════════════════
# INSTRUMENT MAP  symbol → instrument_token
# Kite requires numeric token to fetch historical data.
# Cached at kite/instruments.json, refreshed once per day.
# ════════════════════════════════════════════════════════════════════
def load_instrument_map(kite, force_refresh=False):
    """Return {symbol: token} dict for NSE EQ instruments.
    Uses cached file if fresh (< 24h), else downloads from Kite.
    """
    # Try cache first
    if not force_refresh and os.path.exists(INSTRUMENTS_FILE):
        try:
            with open(INSTRUMENTS_FILE) as f:
                data = json.load(f)
            age = time.time() - data.get('saved_ts', 0)
            if age < INSTRUMENTS_TTL:
                token_map = {k: int(v) for k, v in data.get('map', {}).items()}
                if token_map:
                    print(f'[KITE] Instrument map from cache: {len(token_map)} symbols')
                    return token_map
        except Exception:
            pass

    # Download from Kite
    print('[KITE] Downloading instrument list from Kite...')
    try:
        instruments = kite.instruments('NSE')
    except Exception as e:
        print(f'[KITE] instruments() failed: {e}')
        # Fall back to cache even if stale
        if os.path.exists(INSTRUMENTS_FILE):
            with open(INSTRUMENTS_FILE) as f:
                data = json.load(f)
            return {k: int(v) for k, v in data.get('map', {}).items()}
        return {}

    token_map = {}
    name_map  = {}
    for inst in instruments:
        sym = inst.get('tradingsymbol', '').strip()
        tok = inst.get('instrument_token')
        if sym and tok:
            token_map[sym] = int(tok)
            name_map[sym]  = inst.get('name', sym)

    print(f'[KITE] Instrument map: {len(token_map)} NSE symbols')

    try:
        with open(INSTRUMENTS_FILE, 'w') as f:
            json.dump({
                'saved_at': datetime.datetime.now().isoformat(),
                'saved_ts': time.time(),
                'count':    len(token_map),
                'map':      token_map,
                'names':    name_map,
            }, f)
    except Exception as e:
        print(f'[KITE] Instrument cache write failed: {e}')

    return token_map


# ════════════════════════════════════════════════════════════════════
# NSE UNIVERSE — load from data/raw/nse.json
# ════════════════════════════════════════════════════════════════════
def load_universe():
    """Load NSE universe from data/raw/nse.json.
    Returns list of {symbol, board} dicts, or None if not found.
    """
    if not os.path.exists(NSE_FILE):
        return None
    try:
        with open(NSE_FILE) as f:
            data = json.load(f)
        universe = data.get('universe', [])
        print(f'[KITE] NSE universe loaded: {len(universe)} symbols')
        return universe
    except Exception as e:
        print(f'[KITE] Failed to load NSE universe: {e}')
        return None


# ════════════════════════════════════════════════════════════════════
# OHLCV FETCH — one symbol
# ════════════════════════════════════════════════════════════════════
def fetch_ohlcv(kite, token, symbol):
    """Fetch HISTORY_DAYS of daily OHLCV for one instrument.
    Returns list of [date_str, open, high, low, close, volume] or None.
    """
    to_dt   = get_ist().date()
    from_dt = to_dt - datetime.timedelta(days=HISTORY_DAYS)
    try:
        records = kite.historical_data(
            token,
            from_date=from_dt,
            to_date=to_dt,
            interval='day',
        )
        if not records:
            return None

        rows = []
        for r in records:
            try:
                d = r['date']
                if hasattr(d, 'date'):
                    d = d.date().isoformat()
                else:
                    d = str(d)[:10]
                rows.append([
                    d,
                    round(float(r['open']),   2),
                    round(float(r['high']),   2),
                    round(float(r['low']),    2),
                    round(float(r['close']),  2),
                    int(r['volume']),
                ])
            except Exception:
                continue

        if len(rows) < 30:
            return None

        return rows

    except Exception as e:
        print(f'[KITE] history {symbol}: {e}')
        return None


# ════════════════════════════════════════════════════════════════════
# FULL SCAN — write data/raw/kite.json
# ════════════════════════════════════════════════════════════════════
def run_scan(test_symbols=None):
    """Fetch OHLCV for all NSE universe symbols. Exit 0 on success, 1 on fatal."""
    t0 = time.time()
    write_status('running', message='Loading Kite auth and universe...')

    # Auth
    try:
        kite = load_kite()
    except RuntimeError as e:
        print(f'[KITE] AUTH: {e}')
        write_status('error', message=str(e))
        sys.exit(2)  # auth required

    # Universe
    universe = load_universe()
    if universe is None:
        msg = 'NSE universe not found — run nse_worker.py --universe first'
        print(f'[KITE] {msg}')
        write_status('error', message=msg)
        sys.exit(1)

    # Instrument map
    token_map = load_instrument_map(kite)
    if not token_map:
        msg = 'Instrument map empty — Kite API may be down'
        print(f'[KITE] {msg}')
        write_status('error', message=msg)
        sys.exit(1)

    # Determine symbols to scan
    if test_symbols:
        symbols = test_symbols
        print(f'[KITE] Test mode: {symbols}')
    else:
        symbols = [u['symbol'] for u in universe]
        print(f'[KITE] Scanning {len(symbols)} symbols from NSE universe')

    board_map = {u['symbol']: u.get('board', 'main') for u in universe}

    # Scan
    total    = len(symbols)
    ohlcv    = {}   # symbol → {token, board, rows}
    skipped  = 0    # no token in Kite
    failed   = 0    # API error

    write_status('running', message=f'Fetching OHLCV for {total} stocks...')

    for i, sym in enumerate(symbols, 1):
        token = token_map.get(sym)
        if not token:
            # Try with -SM suffix for SME stocks
            token = token_map.get(sym + '-SM')
            sym_kite = sym + '-SM' if token else sym
        else:
            sym_kite = sym

        if not token:
            skipped += 1
            continue

        time.sleep(HIST_DELAY)
        rows = fetch_ohlcv(kite, token, sym)

        if rows:
            ohlcv[sym] = {
                'kite_symbol': sym_kite,
                'token':       token,
                'board':       board_map.get(sym, 'main'),
                'rows':        rows,
            }
        else:
            failed += 1

        if i % 100 == 0 or i == total:
            print(f'[KITE] {i}/{total} — {len(ohlcv)} ok, {skipped} no-token, {failed} failed')
            write_status('running', count=len(ohlcv), errors=failed,
                         duration=time.time() - t0,
                         message=f'Scanned {i}/{total}...')

    duration = time.time() - t0

    if len(ohlcv) == 0:
        msg = f'Zero stocks fetched — all {total} failed'
        print(f'[KITE] FATAL: {msg}')
        write_status('error', errors=failed, duration=duration, message=msg)
        sys.exit(1)

    fail_rate = failed / total if total else 0
    if fail_rate > 0.20:
        msg = f'High failure rate: {failed}/{total} ({fail_rate:.0%})'
        print(f'[KITE] WARN: {msg}')

    # Save raw output
    raw_data = {
        'saved_at':    get_ist().isoformat(),
        'total':       total,
        'fetched':     len(ohlcv),
        'skipped':     skipped,
        'failed':      failed,
        'ohlcv':       ohlcv,
    }

    try:
        with open(RAW_FILE, 'w', encoding='utf-8') as f:
            json.dump(raw_data, f, ensure_ascii=False)
        print(f'[KITE] Raw saved: {len(ohlcv)} stocks → {RAW_FILE}')
    except Exception as e:
        msg = f'Failed to write {RAW_FILE}: {e}'
        print(f'[KITE] FATAL: {msg}')
        write_status('error', duration=duration, message=msg)
        sys.exit(1)

    write_status(
        'done',
        count=len(ohlcv),
        errors=failed,
        duration=duration,
        message=f'OHLCV saved: {len(ohlcv)} stocks, {skipped} no-token, {failed} errors',
    )
    print(f'[KITE] Scan done: {len(ohlcv)} stocks in {duration:.0f}s')
    sys.exit(0)


# ════════════════════════════════════════════════════════════════════
# PRICE REFRESH — patch data/computed/stocks.json with live LTP
# Runs every 5 min during market hours (called by server or scheduler).
# Does NOT re-run full compute — just updates price, change, upsidePct.
# ════════════════════════════════════════════════════════════════════
def run_price_refresh():
    """Fetch live LTP for all stocks in data/computed/stocks.json.
    Updates price, change, upsidePct in-place. Exit 0 on success, 1 on error.
    """
    t0 = time.time()

    if not is_market_hours():
        print('[KITE] Price refresh: outside market hours — skipping')
        sys.exit(0)

    # Auth
    try:
        kite = load_kite()
    except RuntimeError as e:
        print(f'[KITE] Price refresh auth: {e}')
        sys.exit(2)

    # Load computed stocks
    if not os.path.exists(COMPUTED_FILE):
        print(f'[KITE] Price refresh: {COMPUTED_FILE} not found — run full scan first')
        sys.exit(1)

    try:
        with open(COMPUTED_FILE) as f:
            computed = json.load(f)
        stocks = computed.get('stocks', [])
        if not stocks:
            print('[KITE] Price refresh: no stocks in computed file')
            sys.exit(0)
    except Exception as e:
        print(f'[KITE] Price refresh: read error: {e}')
        sys.exit(1)

    print(f'[KITE] Price refresh: {len(stocks)} stocks...')

    # Build instrument key list and prev_close map
    instrument_keys = []
    prev_map = {}   # symbol → stored price (yesterday's close from last full scan)
    for s in stocks:
        sym = s.get('ticker', '')
        if sym:
            instrument_keys.append(f'NSE:{sym}')
            prev_map[sym] = s.get('price', 0)

    # Batch LTP calls (500 per call)
    ltp_map = {}
    for i in range(0, len(instrument_keys), LTP_BATCH):
        batch = instrument_keys[i:i + LTP_BATCH]
        try:
            result = kite.ltp(batch)
            for key, val in result.items():
                sym = key.replace('NSE:', '')
                ltp = val.get('last_price', 0)
                if ltp:
                    ltp_map[sym] = round(float(ltp), 2)
        except Exception as e:
            print(f'[KITE] ltp() batch error: {e}')
        time.sleep(0.2)

    # Patch stocks
    updated = 0
    for s in stocks:
        sym = s.get('ticker', '')
        ltp = ltp_map.get(sym)
        if not ltp:
            continue

        prev = prev_map.get(sym, ltp)
        s['price']  = ltp
        s['change'] = round((ltp - prev) / prev * 100, 2) if prev else 0.0

        # Recalculate upsidePct from stored targetPrice
        tp = s.get('targetPrice')
        if tp and tp > 0 and ltp > 0:
            s['upsidePct'] = round((tp - ltp) / ltp * 100, 1)

        updated += 1

    # Fetch Nifty 50 + Sensex and save alongside stocks
    try:
        idx_keys = ['NSE:NIFTY 50', 'BSE:SENSEX']
        idx_data = kite.ltp(idx_keys)
        indices  = {}
        for key, label in [('NSE:NIFTY 50', 'NIFTY 50'), ('BSE:SENSEX', 'SENSEX')]:
            entry = idx_data.get(key, {})
            last  = round(float(entry.get('last_price') or 0), 2)
            prev  = round(float((entry.get('ohlc') or {}).get('close') or last), 2)
            chg   = round((last - prev) / prev * 100, 2) if prev else 0.0
            indices[label] = {'price': last, 'change': chg}
        computed['indices'] = indices
        print(f'[KITE] Indices: NIFTY {indices.get("NIFTY 50", {}).get("price","?")}  SENSEX {indices.get("SENSEX", {}).get("price","?")}')
    except Exception as e:
        print(f'[KITE] Index fetch error (non-fatal): {e}')

    # Save patched computed file
    try:
        computed['price_refreshed_at'] = get_ist().isoformat()
        raw_str = json.dumps(computed, ensure_ascii=False)
        # Guard against Infinity/NaN leaking into JSON
        raw_str = raw_str.replace('Infinity', 'null').replace('NaN', 'null')
        with open(COMPUTED_FILE, 'w', encoding='utf-8') as f:
            f.write(raw_str)
    except Exception as e:
        print(f'[KITE] Price refresh write error: {e}')
        sys.exit(1)

    duration = round(time.time() - t0, 1)
    print(f'[KITE] Price refresh done: {updated}/{len(stocks)} updated in {duration}s')
    sys.exit(0)


# ════════════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--scan',          action='store_true', help='Full OHLCV scan')
    parser.add_argument('--price-refresh', action='store_true', help='Live price patch (market hours only)')
    parser.add_argument('--test',          action='store_true', help='Test mode: 5 tickers, scan only')
    args = parser.parse_args()

    if not any([args.scan, args.price_refresh, args.test]):
        print('Usage: python kite_worker.py --scan | --price-refresh | --test')
        sys.exit(1)

    if args.price_refresh:
        run_price_refresh()
    elif args.test:
        run_scan(test_symbols=['RELIANCE', 'TCS', 'INFY', 'HDFCBANK', 'WIPRO'])
    elif args.scan:
        run_scan()


if __name__ == '__main__':
    main()
