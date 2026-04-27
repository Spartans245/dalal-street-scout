"""
db_writer.py — stocks_master Writer
=====================================
Reads data/computed/stocks.json and upserts all stock rows into the
stocks_master table in data/dalal_street.db.
One row per (ticker, trading_date) — the master source of truth for all stocks.

- Skips chartPrices / chartDates (redundant once DB has history)
- Booleans stored as INTEGER (0/1)
- List/dict fields (catalysts, srResistance, srSupport, prevStages) stored as JSON text
- Primary key: (ticker, trading_date) — safe to re-run; overwrites same-day row
- trading_date derived from stocks.json saved_at (not system clock)

Exit codes: 0 = success, 1 = error
"""

import sys, os, json, sqlite3, datetime

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
COMPUTED_FILE = os.path.join(BASE_DIR, 'data', 'computed', 'stocks.json')
DB_FILE       = os.path.join(BASE_DIR, 'data', 'dalal_street.db')

# Fields to skip entirely (large arrays, redundant once DB has history)
SKIP_FIELDS = {'chartPrices', 'chartDates'}

# Fields whose values are list or dict — serialise to JSON text
JSON_FIELDS = {'catalysts', 'srResistance', 'srSupport', 'prevStages'}

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS stocks_master (
    /* master table — one row per (ticker, trading_date), all 71 computed fields */
    ticker              TEXT    NOT NULL,
    trading_date        TEXT    NOT NULL,
    name                TEXT,
    sector              TEXT,
    board               TEXT,
    price               REAL,
    prevClose           REAL,
    change              REAL,
    pe                  REAL,
    mcap                INTEGER,
    promoterHolding     INTEGER,
    pledging            INTEGER,
    debtEq              REAL,
    roe                 REAL,
    roeWarn             TEXT,
    deFlag              TEXT,
    roeFlag             TEXT,
    wk38High            REAL,
    wk38Low             REAL,
    wk52High            REAL,
    wk52Low             REAL,
    near52High          INTEGER,
    pctFrom38High       REAL,
    pctFrom38Low        REAL,
    rsi                 REAL,
    adx                 REAL,
    macd                INTEGER,
    emaSignal           TEXT,
    emaCross            INTEGER,
    emaCrossDays        INTEGER,
    emaTrend            INTEGER,
    volConfirm          INTEGER,
    crossScore          INTEGER,
    emaPreCross         INTEGER,
    emaPostCross        INTEGER,
    emaPullback         INTEGER,
    golden              INTEGER,
    vpbScore            INTEGER,
    vpbDetail           TEXT,
    vpbRangeHeight      REAL,
    avg20Vol            REAL,
    priceCoiling        INTEGER,
    volShrinking        INTEGER,
    volRatio            REAL,
    closePos            REAL,
    ema14               REAL,
    ema50               REAL,
    emaGapPct           REAL,
    ema14Rising         INTEGER,
    ema14RisingFast     INTEGER,
    near38High          INTEGER,
    stage               TEXT,
    catalysts           TEXT,
    dailyVol            REAL,
    score               INTEGER,
    fScore              INTEGER,
    cScore              INTEGER,
    tScore              INTEGER,
    ctScore             INTEGER,
    lScore              INTEGER,
    ath                 REAL,
    mmTarget            REAL,
    targetPrice         REAL,
    targetType          TEXT,
    upsidePct           REAL,
    upsideRs            REAL,
    mmConditional       INTEGER,
    data_source         TEXT,
    srResistance        TEXT,
    srSupport           TEXT,
    prevStages          TEXT,
    sectorMedianPe      REAL,
    peVsSector          REAL,
    sigScore            REAL,
    PRIMARY KEY (ticker, trading_date)
)
"""

# Ordered column list (must match CREATE TABLE above, minus ticker + trading_date)
COLUMNS = [
    'name', 'sector', 'board', 'price', 'prevClose', 'change', 'pe', 'mcap',
    'promoterHolding', 'pledging', 'debtEq', 'roe', 'roeWarn', 'deFlag', 'roeFlag',
    'wk38High', 'wk38Low', 'wk52High', 'wk52Low', 'near52High',
    'pctFrom38High', 'pctFrom38Low', 'rsi', 'adx', 'macd', 'emaSignal',
    'emaCross', 'emaCrossDays', 'emaTrend', 'volConfirm', 'crossScore',
    'emaPreCross', 'emaPostCross', 'emaPullback', 'golden', 'vpbScore', 'vpbDetail',
    'vpbRangeHeight', 'avg20Vol', 'priceCoiling', 'volShrinking', 'volRatio',
    'closePos', 'ema14', 'ema50', 'emaGapPct', 'ema14Rising', 'ema14RisingFast',
    'near38High', 'stage', 'catalysts', 'dailyVol', 'score', 'fScore', 'cScore',
    'tScore', 'ctScore', 'lScore', 'ath', 'mmTarget', 'targetPrice', 'targetType',
    'upsidePct', 'upsideRs', 'mmConditional', 'data_source', 'srResistance',
    'srSupport', 'prevStages', 'sectorMedianPe', 'peVsSector', 'sigScore',
]

BOOL_FIELDS = {
    'near52High', 'macd', 'emaCross', 'emaTrend', 'volConfirm', 'emaPreCross',
    'emaPostCross', 'emaPullback', 'golden', 'priceCoiling', 'volShrinking',
    'ema14Rising', 'ema14RisingFast', 'near38High', 'mmConditional',
}


def coerce(field, value):
    """Convert a field value to a SQLite-compatible type."""
    if value is None:
        return None
    if field in BOOL_FIELDS:
        return 1 if value else 0
    if field in JSON_FIELDS:
        return json.dumps(value, separators=(',', ':'))
    return value


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--date', default=None,
                        help='Override trading_date (YYYY-MM-DD). Use for backfills when saved_at is wrong.')
    args = parser.parse_args()

    t0 = datetime.datetime.now()

    # ── Load stocks.json ──────────────────────────────────────────────
    if not os.path.exists(COMPUTED_FILE):
        print(f'[DB] ERROR: stocks.json not found at {COMPUTED_FILE}')
        sys.exit(1)

    try:
        with open(COMPUTED_FILE, encoding='utf-8') as f:
            payload = json.load(f)
    except Exception as e:
        print(f'[DB] ERROR: failed to read stocks.json — {e}')
        sys.exit(1)

    stocks = payload.get('stocks', [])
    saved_at = payload.get('saved_at', '')

    if not stocks:
        print('[DB] ERROR: stocks list is empty')
        sys.exit(1)

    # Derive trading_date: --date override takes priority, then saved_at, then today
    if args.date:
        trading_date = args.date
        print(f'[DB] trading_date overridden to {trading_date} via --date flag')
    else:
        try:
            trading_date = saved_at[:10]   # '2026-04-10T22:27:45...' → '2026-04-10'
            datetime.date.fromisoformat(trading_date)   # validate
        except Exception:
            print(f'[DB] WARN: could not parse saved_at="{saved_at}", using today as trading_date')
            trading_date = datetime.date.today().isoformat()

    print(f'[DB] trading_date={trading_date}  stocks={len(stocks)}  db={DB_FILE}')

    # ── Open / create DB ──────────────────────────────────────────────
    os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
    try:
        con = sqlite3.connect(DB_FILE)
        con.execute('PRAGMA journal_mode=WAL')
        con.execute(CREATE_TABLE_SQL)
        con.commit()
    except Exception as e:
        print(f'[DB] ERROR: failed to open/create DB — {e}')
        sys.exit(1)

    # ── Build INSERT OR REPLACE statement ─────────────────────────────
    all_cols = ['ticker', 'trading_date'] + COLUMNS
    placeholders = ', '.join(['?'] * len(all_cols))
    col_names = ', '.join(all_cols)
    sql = f'INSERT OR REPLACE INTO stocks_master ({col_names}) VALUES ({placeholders})'

    # ── Upsert rows ───────────────────────────────────────────────────
    rows = []
    skipped = 0
    for s in stocks:
        ticker = s.get('ticker') or s.get('symbol')
        if not ticker:
            skipped += 1
            continue
        row = [ticker, trading_date]
        for col in COLUMNS:
            row.append(coerce(col, s.get(col)))
        rows.append(row)

    try:
        with con:
            con.executemany(sql, rows)
        written = len(rows)
    except Exception as e:
        print(f'[DB] ERROR: failed to write rows — {e}')
        con.close()
        sys.exit(1)

    con.close()

    elapsed = round((datetime.datetime.now() - t0).total_seconds(), 2)
    print(f'[DB] Done: {written} rows written (trading_date={trading_date}) in {elapsed}s'
          + (f'  ({skipped} skipped — no ticker)' if skipped else ''))


if __name__ == '__main__':
    main()
