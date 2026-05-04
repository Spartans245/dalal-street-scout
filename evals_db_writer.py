"""
evals_db_writer.py — EVALS DB Writer (Step B of DB plan)
=========================================================
Reads data/signals_log.json and syncs all signal data into:
  - evals_signals        (one row per signal cycle, upserted by id)
  - evals_daily_prices   (one row per signal×date, upserted by signal_id+trading_date)
  - nifty_eod            (one row per date, upserted by trading_date)

Designed to be run after eval_worker + analytics_worker + lifecycle_worker
complete, so DB is always consistent with signals_log.json.

Safe to re-run — INSERT OR REPLACE on all tables.

Exit codes: 0 = success, 1 = error
"""

import sys, os, json, sqlite3, datetime

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
SIGNALS_FILE  = os.path.join(BASE_DIR, 'data', 'signals_log.json')
DB_FILE       = os.path.join(BASE_DIR, 'data', 'dalal_street.db')

BOOL_FIELDS = {'emaPreCross', 'emaCross', 'emaPullback', 'volConfirm',
               'priceCoiling', 'volShrinking'}

# ── Schema ────────────────────────────────────────────────────────────

CREATE_EVALS_SIGNALS_SQL = """
CREATE TABLE IF NOT EXISTS evals_signals (
    id              TEXT PRIMARY KEY,
    ticker          TEXT NOT NULL,
    name            TEXT,
    sector          TEXT,
    day0            TEXT,
    tier            TEXT,
    tier_date       TEXT,
    stage           TEXT,
    score           INTEGER,
    fScore          INTEGER,
    tScore          INTEGER,
    sigScore        REAL,
    rsi             REAL,
    adx             REAL,
    pe              REAL,
    mcap            INTEGER,
    board           TEXT,
    vpbScore        INTEGER,
    vpbDetail       TEXT,
    crossScore      INTEGER,
    emaPreCross     INTEGER,
    emaCross        INTEGER,
    emaPullback     INTEGER,
    volConfirm      INTEGER,
    priceCoiling    INTEGER,
    volShrinking    INTEGER,
    deFlag          TEXT,
    roeFlag         TEXT,
    price_d0        REAL,
    nifty_d0        REAL,
    source          TEXT,
    outcome         TEXT,
    outcome_day     INTEGER,
    outcome_price   REAL,
    outcome_ret     REAL,
    prev_stages     TEXT,
    stage_history   TEXT,
    retrigger_dates TEXT,
    daily_snapshots TEXT
)
"""

CREATE_EVALS_DAILY_PRICES_SQL = """
CREATE TABLE IF NOT EXISTS evals_daily_prices (
    signal_id       TEXT NOT NULL,
    trading_date    TEXT NOT NULL,
    price           REAL,
    nifty_price     REAL,
    PRIMARY KEY (signal_id, trading_date)
)
"""

CREATE_NIFTY_EOD_SQL = """
CREATE TABLE IF NOT EXISTS nifty_eod (
    trading_date    TEXT PRIMARY KEY,
    close           REAL
)
"""

# Scalar columns on evals_signals (in order, excluding id)
SIGNAL_COLUMNS = [
    'ticker', 'name', 'sector', 'day0', 'tier', 'tier_date', 'stage',
    'score', 'fScore', 'tScore', 'sigScore', 'rsi', 'adx', 'pe', 'mcap',
    'board', 'vpbScore', 'vpbDetail', 'crossScore',
    'emaPreCross', 'emaCross', 'emaPullback', 'volConfirm',
    'priceCoiling', 'volShrinking',
    'deFlag', 'roeFlag', 'price_d0', 'nifty_d0', 'source',
    'outcome', 'outcome_day', 'outcome_price', 'outcome_ret',
    'prev_stages', 'stage_history', 'retrigger_dates', 'daily_snapshots',
]

# JSON-serialised fields
JSON_FIELDS = {'prev_stages', 'stage_history', 'retrigger_dates', 'daily_snapshots'}


def coerce(field, value):
    if value is None:
        return None
    if field in BOOL_FIELDS:
        return 1 if value else 0
    if field in JSON_FIELDS:
        return json.dumps(value, separators=(',', ':'))
    return value


def build_signal_row(s):
    """Return the full (id, col1, col2, ...) tuple for evals_signals INSERT."""
    row = [s.get('id')]
    for col in SIGNAL_COLUMNS:
        row.append(coerce(col, s.get(col)))
    return row


def main():
    t0 = datetime.datetime.now()

    # ── Load signals_log.json ─────────────────────────────────────────
    if not os.path.exists(SIGNALS_FILE):
        print(f'[EVALS_DB] ERROR: signals_log.json not found at {SIGNALS_FILE}')
        sys.exit(1)

    try:
        with open(SIGNALS_FILE, encoding='utf-8') as f:
            payload = json.load(f)
    except Exception as e:
        print(f'[EVALS_DB] ERROR: failed to read signals_log.json — {e}')
        sys.exit(1)

    signals = payload.get('signals', [])
    if not signals:
        print('[EVALS_DB] WARN: signals list is empty — nothing to write')
        sys.exit(0)

    print(f'[EVALS_DB] signals={len(signals)}  db={DB_FILE}')

    # ── Open / create DB ──────────────────────────────────────────────
    os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
    try:
        con = sqlite3.connect(DB_FILE)
        con.execute('PRAGMA journal_mode=WAL')
        con.execute(CREATE_EVALS_SIGNALS_SQL)
        con.execute(CREATE_EVALS_DAILY_PRICES_SQL)
        con.execute(CREATE_NIFTY_EOD_SQL)
        con.commit()
    except Exception as e:
        print(f'[EVALS_DB] ERROR: failed to open/create DB — {e}')
        sys.exit(1)

    # ── Build INSERT OR REPLACE statements ────────────────────────────
    all_sig_cols  = ['id'] + SIGNAL_COLUMNS
    sig_placeholders = ', '.join(['?'] * len(all_sig_cols))
    sig_sql = (
        f'INSERT OR REPLACE INTO evals_signals ({", ".join(all_sig_cols)}) '
        f'VALUES ({sig_placeholders})'
    )

    price_sql = (
        'INSERT OR REPLACE INTO evals_daily_prices '
        '(signal_id, trading_date, price, nifty_price) VALUES (?, ?, ?, ?)'
    )

    nifty_sql = (
        'INSERT OR REPLACE INTO nifty_eod (trading_date, close) VALUES (?, ?)'
    )

    # ── Iterate signals ───────────────────────────────────────────────
    sig_rows   = []
    price_rows = []
    nifty_map  = {}   # date → close (last-writer-wins, all sources agree)

    skipped = 0
    for s in signals:
        sig_id = s.get('id')
        if not sig_id:
            skipped += 1
            continue

        sig_rows.append(build_signal_row(s))

        # Prices dict: {date: price}
        prices       = s.get('prices', {}) or {}
        nifty_prices = s.get('nifty_prices', {}) or {}

        for date, price in prices.items():
            nifty_val = nifty_prices.get(date)
            price_rows.append((sig_id, date, price, nifty_val))

        # Collect nifty EOD from nifty_prices (may be sparse — best effort)
        for date, close in nifty_prices.items():
            if close is not None:
                nifty_map[date] = close

    nifty_rows = [(date, close) for date, close in sorted(nifty_map.items())]

    # ── Write to DB ───────────────────────────────────────────────────
    try:
        with con:
            con.executemany(sig_sql, sig_rows)
            con.executemany(price_sql, price_rows)
            con.executemany(nifty_sql, nifty_rows)
    except Exception as e:
        print(f'[EVALS_DB] ERROR: failed to write rows — {e}')
        con.close()
        sys.exit(1)

    con.close()

    elapsed = round((datetime.datetime.now() - t0).total_seconds(), 2)
    print(
        f'[EVALS_DB] Done: {len(sig_rows)} signals, '
        f'{len(price_rows)} price rows, '
        f'{len(nifty_rows)} nifty_eod rows '
        f'in {elapsed}s'
        + (f'  ({skipped} skipped — no id)' if skipped else '')
    )


if __name__ == '__main__':
    main()
