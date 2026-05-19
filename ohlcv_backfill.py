"""
ohlcv_backfill.py — One-time 2-year OHLCV loader
=================================================
Creates and populates the ohlcv_history table in dalal_street.db.
Safe to re-run — uses INSERT OR REPLACE, skips tickers already fully loaded.

Usage:
  python ohlcv_backfill.py            # full run, all stocks
  python ohlcv_backfill.py --test     # 5 tickers only
  python ohlcv_backfill.py --resume   # skip tickers that already have >= 400 rows
"""

import sys, os, json, time, datetime, argparse, sqlite3

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

BASE_DIR         = os.path.dirname(os.path.abspath(__file__))
RAW_DIR          = os.path.join(BASE_DIR, 'data', 'raw')
KITE_DIR         = os.path.join(BASE_DIR, 'kite')
DB_PATH          = os.path.join(BASE_DIR, 'data', 'dalal_street.db')
NSE_FILE         = os.path.join(RAW_DIR, 'nse.json')
INSTRUMENTS_FILE = os.path.join(KITE_DIR, 'instruments.json')
TOKEN_FILE       = os.path.join(BASE_DIR, 'kite_token.json')
CONFIG_FILE      = os.path.join(BASE_DIR, 'kite_config.json')

HISTORY_DAYS = 730   # 2 years
HIST_DELAY   = 0.35  # seconds between Kite API calls (max 3 req/s)

# Minimum rows to consider a ticker "fully loaded" for --resume
RESUME_THRESHOLD = 400  # ~2 years of trading days


def get_ist():
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5, minutes=30)))


# ── DB ──────────────────────────────────────────────────────────────────────

def open_db():
    con = sqlite3.connect(DB_PATH)
    con.execute('PRAGMA journal_mode=WAL')
    con.execute('PRAGMA synchronous=NORMAL')
    con.execute('''
        CREATE TABLE IF NOT EXISTS ohlcv_history (
            ticker   TEXT NOT NULL,
            date     TEXT NOT NULL,
            open     REAL,
            high     REAL,
            low      REAL,
            close    REAL,
            volume   INTEGER,
            PRIMARY KEY (ticker, date)
        )
    ''')
    con.execute('CREATE INDEX IF NOT EXISTS idx_ohlcv_ticker ON ohlcv_history(ticker)')
    con.execute('CREATE INDEX IF NOT EXISTS idx_ohlcv_date   ON ohlcv_history(date)')
    con.commit()
    return con


def get_loaded_counts(con):
    """Return {ticker: row_count} for all tickers already in ohlcv_history."""
    rows = con.execute('SELECT ticker, COUNT(*) FROM ohlcv_history GROUP BY ticker').fetchall()
    return {r[0]: r[1] for r in rows}


def upsert_rows(con, ticker, rows):
    """Insert or replace OHLCV rows for one ticker."""
    with con:
        con.executemany(
            'INSERT OR REPLACE INTO ohlcv_history (ticker, date, open, high, low, close, volume) '
            'VALUES (?, ?, ?, ?, ?, ?, ?)',
            [(ticker, r[0], r[1], r[2], r[3], r[4], r[5]) for r in rows]
        )


# ── Kite auth ───────────────────────────────────────────────────────────────

def load_kite():
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
        raise RuntimeError(f'Kite token stale (token={token_date}, today={today}). Run kite_auth.py')
    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(access_token)
    return kite


def load_instrument_map():
    """Load cached instrument map. Must already exist (kite_worker --scan populates it)."""
    if not os.path.exists(INSTRUMENTS_FILE):
        raise RuntimeError(f'{INSTRUMENTS_FILE} not found — run kite_worker.py --scan first')
    with open(INSTRUMENTS_FILE) as f:
        data = json.load(f)
    token_map = {k: int(v) for k, v in data.get('map', {}).items()}
    print(f'[OHLCV] Instrument map: {len(token_map)} symbols')
    return token_map


def load_universe():
    if not os.path.exists(NSE_FILE):
        raise RuntimeError(f'{NSE_FILE} not found — run nse_worker.py --universe first')
    with open(NSE_FILE) as f:
        data = json.load(f)
    universe = data.get('universe', [])
    print(f'[OHLCV] NSE universe: {len(universe)} symbols')
    return universe


# ── Fetch ────────────────────────────────────────────────────────────────────

def fetch_ohlcv(kite, token, symbol):
    to_dt   = get_ist().date()
    from_dt = to_dt - datetime.timedelta(days=HISTORY_DAYS)
    try:
        records = kite.historical_data(token, from_date=from_dt, to_date=to_dt, interval='day')
        if not records:
            return None
        rows = []
        for r in records:
            try:
                d = r['date']
                d = d.date().isoformat() if hasattr(d, 'date') else str(d)[:10]
                rows.append([d, round(float(r['open']), 2), round(float(r['high']), 2),
                               round(float(r['low']), 2), round(float(r['close']), 2),
                               int(r['volume'])])
            except Exception:
                continue
        return rows if len(rows) >= 30 else None
    except Exception as e:
        print(f'[OHLCV] history {symbol}: {e}')
        return None


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--test',   action='store_true', help='5 tickers only')
    parser.add_argument('--resume', action='store_true', help='Skip tickers already having >= 400 rows')
    args = parser.parse_args()

    t0 = time.time()
    print(f'[OHLCV] Starting backfill — {HISTORY_DAYS} calendar days (~2 years)')

    kite       = load_kite()
    token_map  = load_instrument_map()
    universe   = load_universe()
    con        = open_db()

    symbols = [u['symbol'] for u in universe]
    if args.test:
        symbols = ['RELIANCE', 'TCS', 'INFY', 'HDFCBANK', 'WIPRO']
        print(f'[OHLCV] Test mode: {symbols}')

    # --resume: skip tickers already fully loaded
    skip_set = set()
    if args.resume:
        loaded = get_loaded_counts(con)
        skip_set = {sym for sym, cnt in loaded.items() if cnt >= RESUME_THRESHOLD}
        print(f'[OHLCV] Resume mode: skipping {len(skip_set)} already-loaded tickers')

    total   = len(symbols)
    ok      = 0
    skipped = 0   # no kite token
    resumed = 0   # skipped via --resume
    failed  = 0

    for i, sym in enumerate(symbols, 1):
        if sym in skip_set:
            resumed += 1
            continue

        # Resolve token (try suffix variants for SME/T2T/BE)
        token = token_map.get(sym)
        if not token:
            for suffix in ('-SM', '-ST', '-BE'):
                token = token_map.get(sym + suffix)
                if token:
                    break
        if not token:
            skipped += 1
            continue

        time.sleep(HIST_DELAY)
        rows = fetch_ohlcv(kite, token, sym)

        if rows:
            upsert_rows(con, sym, rows)
            ok += 1
        else:
            failed += 1

        if i % 100 == 0 or i == total:
            elapsed = time.time() - t0
            remaining = (total - i) * HIST_DELAY
            print(f'[OHLCV] {i}/{total} — {ok} ok, {resumed} resumed, {skipped} no-token, {failed} failed '
                  f'| elapsed {elapsed/60:.1f}m, ~{remaining/60:.1f}m left')

    con.close()
    duration = time.time() - t0
    print(f'\n[OHLCV] Done: {ok} stocks loaded, {failed} failed, {skipped} no-token in {duration/60:.1f} min')
    print(f'[OHLCV] ohlcv_history table ready in {DB_PATH}')


if __name__ == '__main__':
    main()
