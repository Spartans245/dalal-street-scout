"""
backfill_stocks_master.py
=========================
Recomputes and inserts stocks_master rows for historical trading dates
that are missing from the DB. Uses raw OHLCV from kite.json (sliced to
each target date) so technicals are computed with correct history.

Fundamentals (NSE PE/mcap/sector, YF D/E/ROE) are static — today's values
are used for all backfill dates since we don't have historical fundamentals.

Usage:
  python backfill_stocks_master.py               # fills all missing dates Mar 24+
  python backfill_stocks_master.py --from 2026-03-24
  python backfill_stocks_master.py --date 2026-04-07   # single date
"""

import sys, os, json, sqlite3, datetime, argparse

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
KITE_FILE     = os.path.join(BASE_DIR, 'data', 'raw', 'kite.json')
NSE_FILE      = os.path.join(BASE_DIR, 'data', 'raw', 'nse.json')
YF_FILE       = os.path.join(BASE_DIR, 'data', 'raw', 'yf.json')
DB_FILE       = os.path.join(BASE_DIR, 'data', 'dalal_street.db')

sys.path.insert(0, BASE_DIR)

import warnings
warnings.filterwarnings('ignore')

try:
    import pandas as pd
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'pandas', '-q'])
    import pandas as pd

# Import compute logic
from compute import compute_one, load_nse_raw, load_yf_raw
from compute import enrich_sector_pe
from shared.technicals import _safe_json
from db_writer import COLUMNS, BOOL_FIELDS, JSON_FIELDS, coerce


def get_trading_dates_in_kite(ohlcv, from_date, to_date):
    """Return sorted list of trading dates present in kite.json within range."""
    dates = set()
    for ticker, entry in ohlcv.items():
        for row in entry.get('rows', []):
            d = row[0]
            if from_date <= d <= to_date:
                dates.add(d)
    return sorted(dates)


def slice_ohlcv_to_date(ohlcv, target_date):
    """Return a copy of ohlcv with each stock's rows sliced up to and including target_date."""
    sliced = {}
    for ticker, entry in ohlcv.items():
        rows = [r for r in entry.get('rows', []) if r[0] <= target_date]
        if rows:
            sliced[ticker] = {**entry, 'rows': rows}
    return sliced


def get_existing_dates(con):
    """Return set of trading_dates already in stocks_master."""
    cur = con.execute('SELECT DISTINCT trading_date FROM stocks_master')
    return {row[0] for row in cur.fetchall()}


def insert_rows(con, results, trading_date):
    all_cols = ['ticker', 'trading_date'] + COLUMNS
    placeholders = ', '.join(['?'] * len(all_cols))
    col_names = ', '.join(all_cols)
    sql = f'INSERT OR REPLACE INTO stocks_master ({col_names}) VALUES ({placeholders})'
    rows = []
    for s in results:
        ticker = s.get('ticker')
        if not ticker:
            continue
        row = [ticker, trading_date]
        for col in COLUMNS:
            row.append(coerce(col, s.get(col)))
        rows.append(row)
    with con:
        con.executemany(sql, rows)
    return len(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--from', dest='from_date', default='2026-03-24',
                        help='Start date (YYYY-MM-DD). Default: 2026-03-24')
    parser.add_argument('--date', default=None,
                        help='Single date to backfill (YYYY-MM-DD)')
    args = parser.parse_args()

    t0 = datetime.datetime.now()

    # ── Load raw data ─────────────────────────────────────────────────
    print('[BACKFILL] Loading raw data...')
    if not os.path.exists(KITE_FILE):
        print(f'[BACKFILL] ERROR: kite.json not found')
        sys.exit(1)
    with open(KITE_FILE, encoding='utf-8') as f:
        kite_data = json.load(f)
    ohlcv = kite_data.get('ohlcv', {})
    print(f'[BACKFILL] kite.json: {len(ohlcv)} stocks')

    nse_fund = load_nse_raw()
    yf_fund  = load_yf_raw()

    # ── Connect to DB ─────────────────────────────────────────────────
    con = sqlite3.connect(DB_FILE)
    con.execute('PRAGMA journal_mode=WAL')
    existing = get_existing_dates(con)
    print(f'[BACKFILL] Existing dates in stocks_master: {sorted(existing)}')

    # ── Determine target dates ────────────────────────────────────────
    today = datetime.date.today().isoformat()
    if args.date:
        target_dates = [args.date]
    else:
        all_dates = get_trading_dates_in_kite(ohlcv, args.from_date, today)
        target_dates = [d for d in all_dates if d not in existing]

    if not target_dates:
        print('[BACKFILL] Nothing to backfill — all dates already present.')
        con.close()
        return

    print(f'[BACKFILL] Dates to backfill: {target_dates}')

    # ── Backfill each date ────────────────────────────────────────────
    for trading_date in target_dates:
        print(f'[BACKFILL] Computing {trading_date}...')
        sliced = slice_ohlcv_to_date(ohlcv, trading_date)

        results = []
        errors  = 0
        for sym, entry in sliced.items():
            stock = compute_one(sym, entry, nse_fund, yf_fund)
            if stock:
                results.append(stock)
            else:
                errors += 1

        if not results:
            print(f'[BACKFILL] WARN: no results for {trading_date} — skipping')
            continue

        # Sector PE enrichment (same as compute.py)
        enrich_sector_pe(results)

        written = insert_rows(con, results, trading_date)
        print(f'[BACKFILL] {trading_date}: {written} rows written ({errors} errors)')

    con.close()

    elapsed = round((datetime.datetime.now() - t0).total_seconds(), 1)
    print(f'[BACKFILL] Done in {elapsed}s')


if __name__ == '__main__':
    main()
