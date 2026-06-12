"""
eval_worker.py — EVALS Signal Tracker
======================================
Dedicated worker for all EVALS-specific operations.
NEVER modifies scanner output files — reads them only.

Modes:
  python eval_worker.py --eod            # EOD: log new signals + full update
  python eval_worker.py --price-refresh  # Intraday: update prices for open signals

Reads (never writes):
  data/dalal_street.db       — stocks_live view (current prices, stages, scores)

Writes (EVALS-owned only):
  data/dalal_street.db       — evals_signals + evals_daily_prices tables
  data/signals_log.json      — backup copy

Rules:
  - Never import or modify compute.py / kite_worker.py / nse_worker.py
  - Never write to data/raw/* or data/computed/stocks.json
  - One OPEN signal per ticker at all times — if already OPEN, add retrigger
  - Selected set = top-3 per stage box + all-tab top-3, same sort as scanner UI
"""

import sys, os, json, datetime, argparse
from collections import defaultdict

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

import sqlite3

BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
DB_FILE       = os.path.join(BASE_DIR, 'data', 'dalal_street.db')
SIGNALS_FILE  = os.path.join(BASE_DIR, 'data', 'signals_log.json')
STATUS_FILE   = os.path.join(BASE_DIR, 'data', 'status', 'evals.json')

_DB_JSON_FIELDS = {'catalysts', 'srResistance', 'srSupport', 'prevStages'}
_DB_BOOL_FIELDS = {
    'near52High', 'macd', 'emaCross', 'emaTrend', 'volConfirm', 'emaPreCross',
    'emaPostCross', 'emaPullback', 'golden', 'priceCoiling', 'volShrinking',
    'ema14Rising', 'ema14RisingFast', 'near38High', 'mmConditional',
}

def _db_row_to_dict(cur, row):
    cols = [d[0] for d in cur.description]
    d = {}
    for col, val in zip(cols, row):
        if col in _DB_JSON_FIELDS:
            try:
                d[col] = json.loads(val) if val else []
            except Exception:
                d[col] = []
        elif col in _DB_BOOL_FIELDS:
            d[col] = bool(val) if val is not None else False
        else:
            d[col] = val
    return d

def _write_status(status, count=None, errors=0, duration=None, message=''):
    try:
        obj = {
            'status':   status,
            'saved_at': datetime.datetime.now().isoformat(),
            'count':    count,
            'errors':   errors,
            'duration': duration,
            'message':  message,
        }
        os.makedirs(os.path.dirname(STATUS_FILE), exist_ok=True)
        with open(STATUS_FILE, 'w', encoding='utf-8') as f:
            json.dump(obj, f)
    except Exception as e:
        print(f'[EVAL] Could not write status: {e}')


# ── CLI date/force overrides ──────────────────────────────────────────────────

_DATE_OVERRIDE = None
_FORCE         = False

def today_str():
    return _DATE_OVERRIDE or datetime.date.today().isoformat()

def today_dt():
    if _DATE_OVERRIDE:
        return datetime.date.fromisoformat(_DATE_OVERRIDE)
    return datetime.date.today()


# ── NSE holiday check ─────────────────────────────────────────────────────────

_HOLIDAYS_CACHE_FILE = os.path.join(BASE_DIR, 'data', 'evals', 'nse_holidays.json')

def _load_nse_holidays():
    import time as _time
    try:
        if os.path.exists(_HOLIDAYS_CACHE_FILE):
            cached = json.load(open(_HOLIDAYS_CACHE_FILE, encoding='utf-8'))
            if _time.time() - cached.get('fetched_at', 0) < 30 * 86400:
                return set(cached.get('holidays', []))
        import requests as _req
        s = _req.Session()
        s.get('https://www.nseindia.com',
              headers={'User-Agent': 'Mozilla/5.0'}, timeout=8)
        r = s.get('https://www.nseindia.com/api/holiday-master?type=trading',
                  headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json',
                           'Referer': 'https://www.nseindia.com'}, timeout=8)
        raw = r.json().get('CM', [])
        holidays = []
        for h in raw:
            try:
                d = datetime.datetime.strptime(h['tradingDate'], '%d-%b-%Y').date().isoformat()
                holidays.append(d)
            except Exception:
                pass
        os.makedirs(os.path.dirname(_HOLIDAYS_CACHE_FILE), exist_ok=True)
        json.dump({'fetched_at': _time.time(), 'holidays': holidays},
                  open(_HOLIDAYS_CACHE_FILE, 'w'))
        return set(holidays)
    except Exception:
        return set()

def is_trading_day(date_str):
    try:
        d = datetime.date.fromisoformat(date_str)
    except Exception:
        return False
    if d.weekday() >= 5:
        return False
    return date_str not in _load_nse_holidays()


# ── Scanner top-3 selection — mirrors calcPipeScore + sort in index.html ──────

STAGES = ['post_cross', 'pre_cross', 'breakout', 'coiling', 'pullback', 'trending']

def _rsi_score(r):
    if r is None: return 0
    if 45 <= r <= 58:      return 12
    if r > 58 and r <= 65: return 7
    if 40 <= r < 45:       return 4
    if r > 65 and r <= 72: return 2
    return 0

def _adx_score(a):
    if a is None: return 0
    if 20 <= a <= 35: return 10
    if 15 <= a < 20:  return 5
    if a > 35:        return 4
    return 0

def _pipe_score(s):
    return (s.get('fScore') or 0) + _rsi_score(s.get('rsi')) + _adx_score(s.get('adx'))

def _sig_score(s):
    stage = s.get('stage', 'none')
    if stage == 'trending': return 0
    if s.get('emaPreCross') and (s.get('vpbScore') or 0) > 0:
        vs = s.get('vpbScore')
        return 18 if vs >= 10 else (14 if vs >= 7 else 12)
    if (s.get('crossScore') or 0) > 0:
        return s.get('crossScore') + (3 if stage == 'pullback' else 0)
    if (s.get('vpbScore') or 0) > 0:
        return s.get('vpbScore')
    return 0

def _sort_key(s):
    return (
        -_pipe_score(s),
        -(s.get('upsidePct') if s.get('upsidePct') is not None else -999),
        -(s.get('mcap') or 0),
    )

def _all_tab_sort_key(s):
    # All-tab uses pipe_score + sig_score — must match scanner UI and qa_worker
    return (
        -(_pipe_score(s) + _sig_score(s)),
        -(s.get('upsidePct') if s.get('upsidePct') is not None else -999),
        -(s.get('mcap') or 0),
    )

def build_selected(stocks):
    """
    Returns dict: ticker -> stage_label.
    Top-3 per stage box + all-tab top-3 extras. Same sort as scanner UI.
    Phase tabs sort by pipe_score (F+T). All tab sorts by pipe_score+sig_score.
    """
    selected      = {}
    stage_tickers = set()

    for stage in STAGES:
        bucket = sorted([s for s in stocks if s.get('stage') == stage], key=_sort_key)[:3]
        for s in bucket:
            t = s.get('ticker', '')
            if t:
                stage_tickers.add(t)
                selected[t] = stage

    all_sorted = sorted([s for s in stocks if s.get('stage', 'none') != 'none'], key=_all_tab_sort_key)
    for s in all_sorted[:3]:
        t = s.get('ticker', '')
        if t and t not in stage_tickers:
            selected[t] = 'all_tab'

    return selected


# ── Load / save ───────────────────────────────────────────────────────────────

_SIGNAL_JSON_FIELDS = {'prev_stages', 'stage_history', 'retrigger_dates', 'daily_snapshots'}
_SIGNAL_BOOL_FIELDS = {'emaPreCross', 'emaCross', 'emaPullback', 'volConfirm',
                       'priceCoiling', 'volShrinking'}
_SIGNAL_COLUMNS = [
    'ticker', 'name', 'sector', 'day0', 'tier', 'tier_date', 'stage',
    'score', 'fScore', 'tScore', 'sigScore', 'rsi', 'adx', 'pe', 'mcap',
    'board', 'vpbScore', 'vpbDetail', 'crossScore',
    'emaPreCross', 'emaCross', 'emaPullback', 'volConfirm',
    'priceCoiling', 'volShrinking', 'deFlag', 'roeFlag',
    'price_d0', 'nifty_d0', 'source', 'outcome', 'outcome_day',
    'outcome_price', 'outcome_ret', 'prev_stages', 'stage_history',
    'retrigger_dates', 'daily_snapshots',
]

def _coerce_signal(field, value):
    if value is None:
        return None
    if field in _SIGNAL_BOOL_FIELDS:
        return 1 if value else 0
    if field in _SIGNAL_JSON_FIELDS:
        return json.dumps(value, separators=(',', ':'))
    return value

def _signal_row_to_dict(cur, row):
    cols = [d[0] for d in cur.description]
    d = {}
    for col, val in zip(cols, row):
        if col in _SIGNAL_JSON_FIELDS:
            try: d[col] = json.loads(val) if val else ([] if col != 'prev_stages' else {})
            except: d[col] = [] if col != 'prev_stages' else {}
        elif col in _SIGNAL_BOOL_FIELDS:
            d[col] = bool(val) if val is not None else False
        else:
            d[col] = val
    return d

def load_stocks():
    """Read stocks from stocks_live DB. Returns (stocks_list, nifty_price, trading_date)."""
    try:
        con = sqlite3.connect(f'file:{DB_FILE}?mode=ro', uri=True, check_same_thread=False)
        trading_date = (con.execute('SELECT MAX(trading_date) FROM stocks_master').fetchone() or [None])[0]
        cur = con.execute('SELECT * FROM stocks_live')
        stocks = [_db_row_to_dict(cur, row) for row in cur.fetchall()]
        nifty = 0.0
        if trading_date:
            row = con.execute('SELECT close FROM nifty_eod WHERE trading_date=?', (trading_date,)).fetchone()
            nifty = float(row[0]) if row else 0.0
        con.close()
        if not trading_date:
            trading_date = today_str()
        print(f'[EVAL] Loaded {len(stocks)} stocks from DB (trading_date={trading_date})')
        return stocks, nifty, trading_date
    except Exception as e:
        print(f'[EVAL] DB read error: {e}')
        return [], 0, today_str()

def load_signals():
    """Read signals from evals_signals + evals_daily_prices DB tables."""
    try:
        con = sqlite3.connect(DB_FILE)
        con.execute('PRAGMA journal_mode=WAL')
        cur = con.execute('SELECT * FROM evals_signals ORDER BY day0')
        signals = [_signal_row_to_dict(cur, row) for row in cur.fetchall()]
        price_rows = con.execute(
            'SELECT signal_id, trading_date, price, nifty_price FROM evals_daily_prices'
        ).fetchall()
        con.close()
        prices_map = {}
        nifty_map  = {}
        for row in price_rows:
            sid = row[0]
            if sid not in prices_map:
                prices_map[sid] = {}
                nifty_map[sid]  = {}
            if row[2] is not None:
                prices_map[sid][row[1]] = row[2]
            if row[3] is not None:
                nifty_map[sid][row[1]]  = row[3]
        for s in signals:
            sid = s.get('id', '')
            s['prices']       = prices_map.get(sid, {})
            s['nifty_prices'] = nifty_map.get(sid, {})
        print(f'[EVAL] Loaded {len(signals)} signals from DB')
        return signals
    except Exception as e:
        print(f'[EVAL] DB signal read error: {e}')
        if not os.path.exists(SIGNALS_FILE):
            return []
        try:
            with open(SIGNALS_FILE, encoding='utf-8') as f:
                return json.load(f).get('signals', [])
        except Exception:
            return []

def save_signals(signals):
    """Write signals to evals_signals + evals_daily_prices DB tables."""
    try:
        con = sqlite3.connect(DB_FILE)
        con.execute('PRAGMA journal_mode=WAL')
        sig_sql = (
            'INSERT OR REPLACE INTO evals_signals (id, ' +
            ', '.join(_SIGNAL_COLUMNS) + ') VALUES (?' +
            ', ?' * len(_SIGNAL_COLUMNS) + ')'
        )
        price_sql = (
            'INSERT OR REPLACE INTO evals_daily_prices '
            '(signal_id, trading_date, price, nifty_price) VALUES (?, ?, ?, ?)'
        )
        sig_rows   = []
        price_rows = []
        for s in signals:
            if not isinstance(s, dict) or not s.get('id'):
                continue
            row = [s.get('id')]
            for col in _SIGNAL_COLUMNS:
                row.append(_coerce_signal(col, s.get(col)))
            sig_rows.append(row)
            sid = s['id']
            prices       = s.get('prices', {}) or {}
            nifty_prices = s.get('nifty_prices', {}) or {}
            for d in set(prices) | set(nifty_prices):
                price_rows.append((sid, d, prices.get(d), nifty_prices.get(d)))
        with con:
            con.executemany(sig_sql, sig_rows)
            con.executemany(price_sql, price_rows)
        con.close()
        print(f'[EVAL] Saved {len(sig_rows)} signals, {len(price_rows)} price rows to DB')
        # Keep JSON mirror aligned with DB so downstream tools that still read
        # signals_log.json (for example evals_db_writer) cannot reapply stale state.
        _save_signals_json(signals)
    except Exception as e:
        print(f'[EVAL] DB save error: {e} — falling back to JSON')
        _save_signals_json(signals)

def _save_signals_json(signals):
    os.makedirs(os.path.dirname(SIGNALS_FILE), exist_ok=True)
    tmp = SIGNALS_FILE + '.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump({'signals': signals}, f, ensure_ascii=False, indent=2)
        os.replace(tmp, SIGNALS_FILE)
    except Exception as e:
        print(f'[EVAL] Cannot save signals_log.json: {e}')


# ── Outcome computation ───────────────────────────────────────────────────────

WIN_TARGET        = 1.15
LOSS_TARGET       = 0.91
MAX_CALENDAR_DAYS = 60

def compute_outcome(sig):
    """
    Walk sig.prices to find WIN/LOSS/EXPIRED. Skips already-resolved signals.
    Baseline: price on tier_date. WIN=+15%, LOSS=-9%, EXPIRED=60 calendar days.
    """
    if sig.get('outcome') in ('WIN', 'LOSS'):
        return
    if sig.get('outcome') == 'EXPIRED':
        # Re-evaluate if tier_date was updated after expiry was set — expiry
        # window runs from tier_date, so a promotion resets the clock.
        tier_date = sig.get('tier_date') or sig.get('day0', '')
        if not tier_date:
            return
        try:
            expiry_check = datetime.date.fromisoformat(tier_date) + datetime.timedelta(days=MAX_CALENDAR_DAYS)
        except ValueError:
            return
        if datetime.date.today() < expiry_check:
            sig['outcome']       = 'OPEN'
            sig['outcome_day']   = None
            sig['outcome_price'] = None
            sig['outcome_ret']   = None
        else:
            return
    prices_dict = sig.get('prices', {})
    day0        = sig.get('day0', '')
    if not prices_dict or not day0:
        return
    tier_date = sig.get('tier_date') or day0
    p0 = prices_dict.get(tier_date) or sig.get('price_d0', 0)
    if not p0:
        return
    try:
        expiry_date = datetime.date.fromisoformat(tier_date or day0) + datetime.timedelta(days=MAX_CALENDAR_DAYS)
    except ValueError:
        return
    sorted_dates = [d for d in sorted(prices_dict.keys()) if d > tier_date]
    for day_idx, date_str in enumerate(sorted_dates):
        price = prices_dict.get(date_str, 0)
        if not price:
            continue
        if price <= p0 * LOSS_TARGET:
            sig['outcome']       = 'LOSS'
            sig['outcome_day']   = day_idx + 1
            sig['outcome_price'] = price
            sig['outcome_ret']   = round((price - p0) / p0 * 100, 2)
            return
        if price >= p0 * WIN_TARGET:
            sig['outcome']       = 'WIN'
            sig['outcome_day']   = day_idx + 1
            sig['outcome_price'] = price
            sig['outcome_ret']   = round((price - p0) / p0 * 100, 2)
            return
        try:
            if datetime.date.fromisoformat(date_str) >= expiry_date:
                sig['outcome']       = 'EXPIRED'
                sig['outcome_day']   = day_idx + 1
                sig['outcome_price'] = price
                sig['outcome_ret']   = round((price - p0) / p0 * 100, 2)
                return
        except ValueError:
            continue


# ── Tracking window ───────────────────────────────────────────────────────────

def is_within_tracking_window(sig, reference_date=None):
    if sig.get('outcome') in ('WIN', 'LOSS', 'EXPIRED'):
        return False
    ref  = reference_date or today_dt()
    day0 = sig.get('day0', '')
    if not day0:
        return False
    try:
        age = (ref - datetime.date.fromisoformat(day0)).days
        return age <= (MAX_CALENDAR_DAYS + 5)
    except Exception:
        return False


# ── Daily snapshot ────────────────────────────────────────────────────────────

def build_daily_snapshot(s, nifty_price):
    t_score = _rsi_score(s.get('rsi')) + _adx_score(s.get('adx'))
    f_score = s.get('fScore', 0) or 0
    return {
        'price':        round(float(s.get('price',    0) or 0), 2),
        'nifty':        round(float(nifty_price or 0),          2),
        'volume':       round(float(s.get('dailyVol', 0) or 0), 2),
        'avg_volume':   round(float(s.get('avg20Vol', 0) or 0), 1),
        'score':        f_score + t_score,
        'fScore':       f_score,
        'tScore':       t_score,
        'rsi':          round(float(s.get('rsi', 0) or 0), 1),
        'adx':          round(float(s.get('adx', 0) or 0), 1),
        'upsidePct':    s.get('upsidePct', 0) or 0,
        'stage':        s.get('stage', 'none'),
        'vpbScore':     s.get('vpbScore',   0) or 0,
        'crossScore':   s.get('crossScore', 0) or 0,
        'emaPreCross':  bool(s.get('emaPreCross')),
        'emaCross':     bool(s.get('emaCross')),
        'emaPullback':  bool(s.get('emaPullback')),
        'volConfirm':   bool(s.get('volConfirm')),
        'priceCoiling': bool(s.get('priceCoiling')),
        'volShrinking': bool(s.get('volShrinking')),
        'mcap':         s.get('mcap', 0) or 0,
        'pe':           round(float(s.get('pe', 0) or 0), 1),
    }


# ── day_n helper ──────────────────────────────────────────────────────────────

def day_n_for(sig, date_str):
    sorted_dates = sorted(sig.get('prices', {}).keys())
    try:
        return sorted_dates.index(date_str)
    except ValueError:
        try:
            d0 = sorted_dates[0] if sorted_dates else sig.get('day0', date_str)
            return max(0, (datetime.date.fromisoformat(date_str) - datetime.date.fromisoformat(d0)).days)
        except Exception:
            return 0


# ── Core: process selected — new entry OR retrigger ──────────────────────────

def process_selected(signals, selected, stocks, trading_date):
    """
    For each ticker in today's selected set (scanner top-3 per stage):
      - No OPEN signal → create new entry (tier='signal', tier_date=today)
      - Already OPEN   → add retrigger date if not already present
    Never creates a second OPEN signal for the same ticker.
    """
    stock_map  = {s.get('ticker', ''): s for s in stocks}
    open_sigs  = {}
    cycle_count = defaultdict(int)

    for sig in signals:
        t = sig.get('ticker', '')
        cycle_count[t] += 1
        if sig.get('outcome') == 'OPEN':
            prev = open_sigs.get(t)
            if not prev or sig.get('day0', '') > prev.get('day0', ''):
                open_sigs[t] = sig

    new_count       = 0
    promote_count   = 0
    retrigger_count = 0

    for ticker, phase in selected.items():
        s = stock_map.get(ticker)
        if not s:
            continue

        existing = open_sigs.get(ticker)
        f = s.get('fScore', 0) or 0
        t_score = _rsi_score(s.get('rsi')) + _adx_score(s.get('adx'))

        if existing is None:
            cycle   = cycle_count[ticker] + 1
            price   = s.get('price', 0)

            entry = {
                'id':              f'{ticker}_{trading_date}_c{cycle}',
                'ticker':          ticker,
                'name':            s.get('name', ticker),
                'sector':          s.get('sector', 'Others'),
                'board':           s.get('board', 'main'),
                'day0':            trading_date,
                'tier':            'signal',
                'tier_date':       trading_date,
                'stage':           s.get('stage', 'none'),
                'source':          'eval_worker',
                'score':           f + t_score,
                'fScore':          f,
                'tScore':          t_score,
                'sigScore':        0,
                'rsi':             round(s.get('rsi', 0) or 0, 1),
                'adx':             round(s.get('adx', 0) or 0, 1),
                'pe':              round(s.get('pe',  0) or 0, 1),
                'mcap':            s.get('mcap',       0) or 0,
                'vpbScore':        s.get('vpbScore',   0) or 0,
                'vpbDetail':       s.get('vpbDetail', 'none'),
                'crossScore':      s.get('crossScore', 0) or 0,
                'emaPreCross':     bool(s.get('emaPreCross')),
                'emaCross':        bool(s.get('emaCross')),
                'emaPullback':     bool(s.get('emaPullback')),
                'volConfirm':      bool(s.get('volConfirm')),
                'priceCoiling':    bool(s.get('priceCoiling')),
                'volShrinking':    bool(s.get('volShrinking')),
                'deFlag':          s.get('deFlag'),
                'roeFlag':         s.get('roeFlag'),
                'price_d0':        price,
                'nifty_d0':        0,
                'prev_stages':     s.get('prevStages', {}),
                'prices':          {trading_date: price} if price else {},
                'nifty_prices':    {},
                'daily_snapshots': {trading_date: build_daily_snapshot(s, 0)},
                'stage_history':   [{'date': trading_date, 'stage': s.get('stage', 'none'), 'day_n': 0}],
                'retrigger_dates': [],
                'outcome':         'OPEN',
                'outcome_day':     None,
                'outcome_price':   None,
                'outcome_ret':     None,
            }
            signals.append(entry)
            open_sigs[ticker]    = entry
            cycle_count[ticker] += 1
            new_count += 1

        else:
            # Existing OPEN baseline now selected by scanner top-3:
            # promote in-place to signal tier (no second OPEN cycle).
            if existing.get('tier') != 'signal':
                existing['tier']      = 'signal'
                existing['tier_date'] = trading_date
                existing['source']    = 'eval_worker'
                # Keep signal context fresh for UI/analytics.
                existing['stage']        = s.get('stage', existing.get('stage', 'none'))
                existing['score']        = f + t_score
                existing['fScore']       = f
                existing['tScore']       = t_score
                existing['rsi']          = round(s.get('rsi', 0) or 0, 1)
                existing['adx']          = round(s.get('adx', 0) or 0, 1)
                existing['pe']           = round(s.get('pe', 0) or 0, 1)
                existing['mcap']         = s.get('mcap', 0) or 0
                existing['vpbScore']     = s.get('vpbScore', 0) or 0
                existing['vpbDetail']    = s.get('vpbDetail', 'none')
                existing['crossScore']   = s.get('crossScore', 0) or 0
                existing['emaPreCross']  = bool(s.get('emaPreCross'))
                existing['emaCross']     = bool(s.get('emaCross'))
                existing['emaPullback']  = bool(s.get('emaPullback'))
                existing['volConfirm']   = bool(s.get('volConfirm'))
                existing['priceCoiling'] = bool(s.get('priceCoiling'))
                existing['volShrinking'] = bool(s.get('volShrinking'))
                existing['deFlag']       = s.get('deFlag')
                existing['roeFlag']      = s.get('roeFlag')
                promote_count += 1
                # Entry day to Stock-Wise: don't mark a retrigger on same date.
                continue

            # Already OPEN — add retrigger (skip if today is the entry day)
            if existing.get('tier_date') == trading_date or existing.get('day0') == trading_date:
                continue
            rdates = existing.setdefault('retrigger_dates', [])
            existing_dates = {r['date'] if isinstance(r, dict) else r for r in rdates}
            if trading_date not in existing_dates:
                rdates.append({'date': trading_date, 'day_n': day_n_for(existing, trading_date)})
                retrigger_count += 1

    print(f'[EVAL] New entries: {new_count} | Promotions: {promote_count} | Retriggers: {retrigger_count}')
    return new_count, retrigger_count


# ── Price update ──────────────────────────────────────────────────────────────

def update_prices(signals, stocks, nifty_price, mode='eod', trading_date=None):
    today     = trading_date or today_str()
    stock_map = {s.get('ticker', ''): s for s in stocks}
    updated   = 0

    for sig in signals:
        if sig is None:
            continue
        if sig.get('outcome') in ('WIN', 'LOSS', 'EXPIRED'):
            continue
        if not is_within_tracking_window(sig):
            continue
        ticker = sig.get('ticker', '')
        s = stock_map.get(ticker)
        if not s:
            continue
        price = s.get('price', 0)
        if price and price > 0:
            sig.setdefault('prices', {})[today] = round(float(price), 2)
            updated += 1
        if nifty_price > 0:
            sig.setdefault('nifty_prices', {})[today] = round(float(nifty_price), 2)
            if not sig.get('nifty_d0'):
                sig['nifty_d0'] = round(float(nifty_price), 2)
        if mode == 'eod':
            snap = build_daily_snapshot(s, nifty_price)
            if not isinstance(sig.get('daily_snapshots'), dict):
                sig['daily_snapshots'] = {}
            sig['daily_snapshots'][today] = snap

    print(f'[EVAL] Prices updated: {updated} open signals ({mode})')
    return updated


# ── Stage history ─────────────────────────────────────────────────────────────

def update_stage_history(signals, stocks, trading_date=None):
    today     = trading_date or today_str()
    stage_map = {s.get('ticker', ''): s.get('stage', 'none') for s in stocks}
    updated   = 0
    for sig in signals:
        if sig.get('outcome') in ('WIN', 'LOSS', 'EXPIRED'):
            continue
        if sig.get('tier') != 'signal':
            continue
        ticker = sig.get('ticker', '')
        current_stage = stage_map.get(ticker)
        if not current_stage:
            continue
        hist = sig.setdefault('stage_history', [])
        if not hist or hist[-1].get('stage') != current_stage:
            hist.append({'date': today, 'stage': current_stage, 'day_n': day_n_for(sig, today)})
            updated += 1
    print(f'[EVAL] Stage history: {updated} transitions recorded')
    return updated


# ── Kite fallback for SME stocks dropped from NSE universe ───────────────────

def update_prices_kite_fallback(signals, trading_date):
    """
    After update_prices() runs, some OPEN signals may still lack today's price
    because their stock dropped out of stocks_master (e.g. SME stocks renamed
    to TICKER-SM by Kite). Fetch ohlc.close via Kite quote() for those signals.
    ohlc.close = previous day's close when called after market hours, which is
    exactly the EOD price we need.
    """
    missing = [
        sig for sig in signals
        if sig.get('outcome') == 'OPEN'
        and sig.get('tier') == 'signal'
        and trading_date not in sig.get('prices', {})
    ]
    if not missing:
        return 0

    try:
        from kite_worker import load_kite
        kite = load_kite()
    except Exception as e:
        print(f'[EVAL] Kite fallback: auth failed — {e}')
        return 0

    # Build instrument keys — try both plain and -SM (SME board)
    key_to_sig = {}
    keys = []
    for sig in missing:
        t = sig['ticker']
        board = sig.get('board', 'main')
        if board == 'sme':
            k = f'NSE:{t}-SM'
        else:
            k = f'NSE:{t}'
        keys.append(k)
        key_to_sig[k] = sig
        # Also try the other variant as fallback
        alt = f'NSE:{t}-SM' if board != 'sme' else f'NSE:{t}'
        if alt not in key_to_sig:
            keys.append(alt)
            key_to_sig[alt] = sig

    updated = 0
    try:
        result = kite.quote(keys)
        for key, val in result.items():
            sig = key_to_sig.get(key)
            if not sig:
                continue
            # ohlc.close = previous session's close (correct EOD price)
            price = val.get('ohlc', {}).get('close', 0)
            if price and price > 0:
                price = round(float(price), 2)
                if trading_date not in sig.get('prices', {}):
                    sig.setdefault('prices', {})[trading_date] = price
                    updated += 1
    except Exception as e:
        print(f'[EVAL] Kite fallback quote error: {e}')

    if updated:
        print(f'[EVAL] Kite fallback: {updated}/{len(missing)} missing prices filled via Kite quote()')
    else:
        print(f'[EVAL] Kite fallback: {len(missing)} signals still missing price after Kite attempt')
    return updated


# ── Entry points ──────────────────────────────────────────────────────────────

def run_eod():
    import time as _time
    _t0 = _time.time()
    print('[EVAL] === EOD run starting ===')
    _write_status('running', message='Starting...')

    stocks, nifty, trading_date = load_stocks()
    if not stocks:
        print('[EVAL] No stocks data — aborting')
        _write_status('error', message='No stocks data')
        return

    if _DATE_OVERRIDE:
        trading_date = _DATE_OVERRIDE
    print(f'[EVAL] Trading date: {trading_date}')

    if not _DATE_OVERRIDE and not _FORCE and not is_trading_day(trading_date):
        print(f'[EVAL] {trading_date} is not a trading day — skipping.')
        return

    signals = load_signals()
    print(f'[EVAL] Loaded {len(signals)} existing signals')

    active_stocks = [s for s in stocks if s.get('stage', 'none') != 'none']
    selected = build_selected(active_stocks)
    print(f'[EVAL] Selected: {len(selected)} stocks from scanner')

    process_selected(signals, selected, stocks, trading_date)
    update_prices(signals, stocks, nifty, mode='eod', trading_date=trading_date)
    update_prices_kite_fallback(signals, trading_date)

    resolved = 0
    for sig in signals:
        if sig.get('outcome') not in ('WIN', 'LOSS', 'EXPIRED'):
            before = sig.get('outcome')
            compute_outcome(sig)
            if sig.get('outcome') != before:
                resolved += 1
    if resolved:
        print(f'[EVAL] Outcomes resolved: {resolved}')

    update_stage_history(signals, stocks, trading_date=trading_date)
    save_signals(signals)

    open_count = sum(1 for s in signals if s.get('outcome') == 'OPEN')
    sig_count  = sum(1 for s in signals if s.get('tier') == 'signal')
    win_count  = sum(1 for s in signals if s.get('outcome') == 'WIN')
    loss_count = sum(1 for s in signals if s.get('outcome') == 'LOSS')
    exp_count  = sum(1 for s in signals if s.get('outcome') == 'EXPIRED')
    elapsed    = round(_time.time() - _t0, 1)
    print(f'[EVAL] Done — total: {len(signals)} | open: {open_count} | WIN: {win_count} | LOSS: {loss_count} | EXPIRED: {exp_count}')
    _write_status('done', count=sig_count, duration=elapsed,
                  message=f'{sig_count} stockwise · {open_count} open signals')


def run_price_refresh():
    print('[EVAL] === Price refresh starting ===')
    stocks, nifty, trading_date = load_stocks()
    if not stocks:
        print('[EVAL] No stocks data — aborting')
        return
    if not is_trading_day(datetime.date.today().isoformat()):
        print(f'[EVAL] Not a trading day — skipping price refresh.')
        return
    signals = load_signals()
    update_prices(signals, stocks, nifty, mode='price-refresh', trading_date=trading_date)
    save_signals(signals)
    print('[EVAL] Price refresh done')


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    import re as _re
    for _i, _a in enumerate(sys.argv):
        if _a == '--date' and _i + 1 < len(sys.argv):
            _d = sys.argv[_i + 1]
            if _re.match(r'^\d{4}-\d{2}-\d{2}$', _d):
                _DATE_OVERRIDE = _d
                print(f'[EVAL] Date override: {_DATE_OVERRIDE}')
            else:
                print(f'[EVAL] Invalid --date format (expected YYYY-MM-DD): {_d}')
                sys.exit(1)
            break

    parser = argparse.ArgumentParser(description='EVALS signal tracker')
    parser.add_argument('--eod',           action='store_true', help='EOD full update')
    parser.add_argument('--price-refresh', action='store_true', help='Intraday price update only')
    parser.add_argument('--date',          default=None, help='Override trading date (YYYY-MM-DD)')
    parser.add_argument('--force',         action='store_true', help='Skip trading-day guard')
    args = parser.parse_args()

    if args.force:
        _FORCE = True
        print('[EVAL] --force: trading-day guard bypassed')

    if args.eod:
        run_eod()
    elif getattr(args, 'price_refresh', False):
        run_price_refresh()
    else:
        print('Usage: eval_worker.py --eod | --price-refresh [--date YYYY-MM-DD] [--force]')
        sys.exit(1)
