"""
backfill_stocks_master.py — Backfill missing stocks_master rows from chartPrices.

For each (ticker, trading_date) pair that exists in chartDates but is missing
from stocks_master, inserts a row with:
  - price from chartPrices (the close for that date)
  - static fields from current stocks.json (name, sector, board, mcap, pe, etc.)
  - all computed fields (rsi, adx, stage, scores etc.) set to NULL

Only fills gaps — does not overwrite existing rows (INSERT OR IGNORE).

Run: python backfill_stocks_master.py
     python backfill_stocks_master.py --from 2026-01-22   # full 60-day history
"""
import sys, os, json, sqlite3, datetime, argparse

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
COMPUTED_FILE = os.path.join(BASE_DIR, 'data', 'computed', 'stocks.json')
DB_FILE       = os.path.join(BASE_DIR, 'data', 'dalal_street.db')

# Static fields we can reliably carry from current stocks.json to historical rows
STATIC_FIELDS = [
    'name', 'sector', 'board', 'pe', 'mcap', 'promoterHolding', 'pledging',
    'debtEq', 'roe', 'roeWarn', 'deFlag', 'roeFlag',
    'wk52High', 'wk52Low',  # these change but are better than NULL
]

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--from', dest='from_date', default='2026-04-10',
                        help='Backfill from this date (YYYY-MM-DD). Default: 2026-04-10 (DB start).')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be inserted without writing.')
    args = parser.parse_args()

    from_date = args.from_date
    print(f'[BACKFILL] Loading stocks.json...')

    with open(COMPUTED_FILE, encoding='utf-8') as f:
        payload = json.load(f)
    stocks = payload.get('stocks', [])
    print(f'[BACKFILL] {len(stocks)} tickers in stocks.json')

    # Build lookup: ticker -> stock dict + chartDates/chartPrices
    ticker_map = {}
    for s in stocks:
        t = s.get('ticker')
        if not t:
            continue
        chart_dates  = s.get('chartDates', [])
        chart_prices = s.get('chartPrices', [])
        if len(chart_dates) != len(chart_prices):
            continue
        ticker_map[t] = {
            'static': {f: s.get(f) for f in STATIC_FIELDS},
            'prices': dict(zip(chart_dates, chart_prices)),
        }

    print(f'[BACKFILL] {len(ticker_map)} tickers with chart history')

    # Connect to DB
    conn = sqlite3.connect(DB_FILE)
    conn.execute('PRAGMA journal_mode=WAL')

    # Find all existing (ticker, trading_date) pairs in DB for fast lookup
    existing = set(
        conn.execute('SELECT ticker, trading_date FROM stocks_master').fetchall()
    )
    print(f'[BACKFILL] {len(existing)} existing rows in stocks_master')

    # Build rows to insert
    rows = []
    dates_seen = set()
    for ticker, info in ticker_map.items():
        for date, price in info['prices'].items():
            if date < from_date:
                continue
            if (ticker, date) in existing:
                continue
            dates_seen.add(date)
            row = {
                'ticker':       ticker,
                'trading_date': date,
                'price':        round(float(price), 2) if price is not None else None,
                'data_source':  'chartPrices_backfill',
            }
            row.update(info['static'])
            rows.append(row)

    print(f'[BACKFILL] {len(rows)} missing rows to insert across dates: {sorted(dates_seen)}')

    if args.dry_run:
        print('[BACKFILL] Dry run — no writes.')
        return

    if not rows:
        print('[BACKFILL] Nothing to insert.')
        conn.close()
        return

    # All columns that could appear
    all_cols = ['ticker', 'trading_date', 'price', 'data_source'] + STATIC_FIELDS
    placeholders = ', '.join(['?'] * len(all_cols))
    col_names = ', '.join(all_cols)
    sql = f'INSERT OR IGNORE INTO stocks_master ({col_names}) VALUES ({placeholders})'

    batch = []
    for row in rows:
        batch.append([row.get(c) for c in all_cols])

    try:
        with conn:
            conn.executemany(sql, batch)
        print(f'[BACKFILL] Inserted {len(batch)} rows.')
    except Exception as e:
        print(f'[BACKFILL] ERROR: {e}')
        conn.close()
        sys.exit(1)

    # Summary by date
    print('[BACKFILL] Rows inserted per date:')
    date_counts = {}
    for row in rows:
        date_counts[row['trading_date']] = date_counts.get(row['trading_date'], 0) + 1
    for d in sorted(date_counts):
        print(f'  {d}: {date_counts[d]} rows')

    conn.close()
    print('[BACKFILL] Done.')

if __name__ == '__main__':
    main()
