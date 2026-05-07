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
  data/computed/stocks.json  — fallback only if DB unavailable

Writes (EVALS-owned only):
  data/dalal_street.db       — evals_signals + evals_daily_prices tables
  data/signals_log.json      — backup copy

Rules:
  - Never import or modify compute.py / kite_worker.py / nse_worker.py
  - Never write to data/raw/* or data/computed/stocks.json
  - One OPEN signal per ticker at all times — if a stock is already OPEN, add retrigger, never a new cycle
  - Scoring uses rsi_score+adx_score (recomputed at runtime), never stored tScore
"""

import sys, os, json, datetime, argparse
from collections import defaultdict

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

import sqlite3

BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
COMPUTED_FILE = os.path.join(BASE_DIR, 'data', 'computed', 'stocks.json')  # fallback only
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


# ── Scoring — identical to qa_worker, recomputed at runtime (never stored tScore) ──

PHASES = ['post_cross', 'pre_cross', 'breakout', 'coiling', 'pullback', 'trending']

def rsi_score(r):
    if r is None: return 0
    if 45 <= r <= 58: return 12
    if 58 < r <= 65:  return 7
    if 40 <= r < 45:  return 4
    if 65 < r <= 72:  return 2
    return 0

def adx_score(a):
    if a is None: return 0
    if 20 <= a <= 35: return 10
    if 15 <= a < 20:  return 5
    if a > 35:        return 4
    return 0

def sig_score(s):
    stage = s.get('stage', 'none')
    if stage == 'trending': return 0
    if s.get('emaPreCross') and (s.get('vpbScore') or 0) > 0:
        vs = s.get('vpbScore')
        return 18 if vs >= 10 else (14 if vs >= 7 else 12)
    if (s.get('crossScore') or 0) > 0:
        return s['crossScore'] + (3 if stage == 'pullback' else 0)
    if (s.get('vpbScore') or 0) > 0:
        return s['vpbScore']
    return 0

def _phase_key(s):
    return (
        -((s.get('fScore') or 0) + rsi_score(s.get('rsi')) + adx_score(s.get('adx'))),
        -(s.get('upsidePct') if s.get('upsidePct') is not None else -999),
        -(s.get('mcap') or 0),
    )

def _all_key(s):
    return (
        -((s.get('fScore') or 0) + rsi_score(s.get('rsi')) + adx_score(s.get('adx')) + sig_score(s)),
        -(s.get('upsidePct') if s.get('upsidePct') is not None else -999),
        -(s.get('mcap') or 0),
    )


# ── Build today's selected set (same as qa_worker) ────────────────────────────

def build_selected(stocks):
    """
    Returns dict: ticker -> phase_label for today's top-3 per phase + all-tab extras.
    Uses recomputed F+T+sig scores, never stored tScore.
    """
    selected = {}
    phase_tickers = set()

    for phase in PHASES:
        bucket = sorted([s for s in stocks if s.get('stage') == phase], key=_phase_key)[:3]
        for s in bucket:
            t = s.get('ticker', '')
            if t:
                phase_tickers.add(t)
                selected[t] = phase

    all_sorted = sorted([s for s in stocks if s.get('stage', 'none') != 'none'], key=_all_key)
    for s in all_sorted[:3]:
        t = s.get('ticker', '')
        if t and t not in phase_tickers:
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
    """Read stocks from stocks_live DB view. Returns (stocks_list, nifty_price, trading_date)."""
    if os.path.exists(DB_FILE):
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
            if not nifty:
                nifty_file = os.path.join(BASE_DIR, 'data', 'evals', 'nifty_close.json')
                if os.path.exists(nifty_file):
                    try:
                        nd = json.load(open(nifty_file, encoding='utf-8'))
                        if nd.get('date') == trading_date:
                            nifty = float(nd.get('close', 0) or 0)
                    except Exception:
                        pass
            if not trading_date:
                trading_date = today_str()
            print(f'[EVAL] Loaded {len(stocks)} stocks from DB (trading_date={trading_date})')
            return stocks, nifty, trading_date
        except Exception as e:
            print(f'[EVAL] DB read error, falling back to stocks.json: {e}')

    # Fallback: stocks.json
    try:
        with open(COMPUTED_FILE, encoding='utf-8') as f:
            raw = f.read()
        raw = raw.replace('Infinity', 'null').replace('NaN', 'null').replace('-null', 'null')
        data, _ = json.JSONDecoder().raw_decode(raw)
        stocks = data.get('stocks', [])
        nifty  = data.get('indices', {}).get('NIFTY 50', {}).get('price', 0) or 0
        trading_date = None
        for key in ('price_refreshed_at', 'last_updated', 'saved_at'):
            ts = data.get(key, '')
            if ts and len(ts) >= 10:
                trading_date = ts[:10]
                break
        trading_date = trading_date or today_str()
        print(f'[EVAL] Loaded {len(stocks)} stocks from stocks.json (fallback)')
        return stocks, nifty, trading_date
    except Exception as e:
        print(f'[EVAL] Cannot read stocks.json: {e}')
        return [], 0, today_str()

def load_signals():
    """Read signals from evals_signals + evals_daily_prices DB tables."""
    if os.path.exists(DB_FILE):
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
            print(f'[EVAL] DB signal read error, falling back to signals_log.json: {e}')

    if not os.path.exists(SIGNALS_FILE):
        return []
    try:
        with open(SIGNALS_FILE, encoding='utf-8') as f:
            return json.load(f).get('signals', [])
    except Exception as e:
        print(f'[EVAL] Cannot read signals_log.json: {e}')
        return []

def save_signals(signals):
    """Write signals to evals_signals + evals_daily_prices DB tables."""
    if not os.path.exists(DB_FILE):
        _save_signals_json(signals)
        return
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
    Baseline: price on tier_date (D0 entry). WIN=+15%, LOSS=-9%, EXPIRED=60 calendar days.
    """
    if sig.get('outcome') in ('WIN', 'LOSS', 'EXPIRED'):
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
        expiry_date = datetime.date.fromisoformat(day0) + datetime.timedelta(days=MAX_CALENDAR_DAYS)
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
    ss = sig_score(s)
    total = (s.get('fScore', 0) or 0) + rsi_score(s.get('rsi')) + adx_score(s.get('adx')) + ss
    return {
        'price':          round(float(s.get('price',      0) or 0), 2),
        'nifty':          round(float(nifty_price or 0),            2),
        'volume':         round(float(s.get('dailyVol',   0) or 0), 2),
        'avg_volume':     round(float(s.get('avg20Vol',   0) or 0), 1),
        'score':          total,
        'fScore':         s.get('fScore',    0) or 0,
        'tScore':         rsi_score(s.get('rsi')) + adx_score(s.get('adx')),
        'sigScore':       ss,
        'rsi':            round(float(s.get('rsi',  0) or 0), 1),
        'adx':            round(float(s.get('adx',  0) or 0), 1),
        'upsidePct':      s.get('upsidePct', 0) or 0,
        'stage':          s.get('stage',    'none'),
        'vpbScore':       s.get('vpbScore',    0) or 0,
        'crossScore':     s.get('crossScore',  0) or 0,
        'emaPreCross':    bool(s.get('emaPreCross')),
        'emaCross':       bool(s.get('emaCross')),
        'emaPullback':    bool(s.get('emaPullback')),
        'volConfirm':     bool(s.get('volConfirm')),
        'priceCoiling':   bool(s.get('priceCoiling')),
        'volShrinking':   bool(s.get('volShrinking')),
        'mcap':           s.get('mcap',     0) or 0,
        'pe':             round(float(s.get('pe',   0) or 0), 1),
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
    For each ticker in today's selected set:
      - If no OPEN signal exists → create a new cycle entry (tier='signal', tier_date=today)
      - If an OPEN signal exists → add retrigger for today if not already present
    Never creates a second OPEN signal for the same ticker.
    """
    stock_map   = {s.get('ticker', ''): s for s in stocks}
    open_sigs   = {}   # ticker -> most recent OPEN signal
    cycle_count = defaultdict(int)

    for sig in signals:
        t = sig.get('ticker', '')
        cycle_count[t] += 1
        if sig.get('outcome') == 'OPEN':
            prev = open_sigs.get(t)
            if not prev or sig.get('day0', '') > prev.get('day0', ''):
                open_sigs[t] = sig

    # Most-recent resolved signal per ticker (for D0 reset on new cycle)
    resolved_latest = {}
    for sig in signals:
        if sig.get('outcome') in ('WIN', 'LOSS', 'EXPIRED'):
            t = sig.get('ticker', '')
            prev = resolved_latest.get(t)
            if not prev or sig.get('day0', '') > prev.get('day0', ''):
                resolved_latest[t] = sig

    new_count       = 0
    retrigger_count = 0

    for ticker, phase in selected.items():
        s = stock_map.get(ticker)
        if not s:
            continue

        existing = open_sigs.get(ticker)

        if existing is None:
            # ── New cycle ──────────────────────────────────────────
            prior = resolved_latest.get(ticker)
            if prior and prior.get('outcome') in ('WIN', 'LOSS'):
                new_d0       = prior.get('outcome_day') and trading_date or trading_date
                # D0 = outcome date of prior cycle
                new_d0       = trading_date   # for new entries today, D0 = today
                new_price_d0 = s.get('price', 0)
            elif prior and prior.get('outcome') == 'EXPIRED':
                new_d0       = trading_date
                new_price_d0 = s.get('price', 0)
            else:
                new_d0       = trading_date
                new_price_d0 = s.get('price', 0)

            cycle   = cycle_count[ticker] + 1
            ss      = sig_score(s)
            f       = s.get('fScore', 0) or 0
            t_score = rsi_score(s.get('rsi')) + adx_score(s.get('adx'))
            total   = f + t_score + ss

            entry = {
                'id':              f'{ticker}_{new_d0}_c{cycle}',
                'ticker':          ticker,
                'name':            s.get('name', ticker),
                'sector':          s.get('sector', 'Others'),
                'board':           s.get('board', 'main'),
                'day0':            new_d0,
                'tier':            'signal',
                'tier_date':       new_d0,
                'stage':           s.get('stage', 'none'),
                'source':          'eval_worker',
                'score':           total,
                'fScore':          f,
                'tScore':          t_score,
                'sigScore':        ss,
                'rsi':             round(s.get('rsi',  0) or 0, 1),
                'adx':             round(s.get('adx',  0) or 0, 1),
                'pe':              round(s.get('pe',   0) or 0, 1),
                'mcap':            s.get('mcap',        0) or 0,
                'board':           s.get('board', 'main'),
                'vpbScore':        s.get('vpbScore',    0) or 0,
                'vpbDetail':       s.get('vpbDetail', 'none'),
                'crossScore':      s.get('crossScore',  0) or 0,
                'emaPreCross':     bool(s.get('emaPreCross')),
                'emaCross':        bool(s.get('emaCross')),
                'emaPullback':     bool(s.get('emaPullback')),
                'volConfirm':      bool(s.get('volConfirm')),
                'priceCoiling':    bool(s.get('priceCoiling')),
                'volShrinking':    bool(s.get('volShrinking')),
                'deFlag':          s.get('deFlag'),
                'roeFlag':         s.get('roeFlag'),
                'price_d0':        new_price_d0,
                'nifty_d0':        0,   # filled by update_prices
                'prev_stages':     s.get('prevStages', {}),
                'prices':          {new_d0: new_price_d0} if new_price_d0 else {},
                'nifty_prices':    {},
                'daily_snapshots': {new_d0: build_daily_snapshot(s, 0)},
                'stage_history':   [{'date': new_d0, 'stage': s.get('stage', 'none'), 'day_n': 0}],
                'retrigger_dates': [],
                'outcome':         'OPEN',
                'outcome_day':     None,
                'outcome_price':   None,
                'outcome_ret':     None,
            }
            signals.append(entry)
            open_sigs[ticker]    = entry   # prevent double-entry within same run
            cycle_count[ticker] += 1
            new_count += 1

        else:
            # ── Retrigger ─────────────────────────────────────────
            # Skip if today IS the tier_date (entry day is not a retrigger)
            if existing.get('tier_date') == trading_date:
                continue
            rdates = existing.setdefault('retrigger_dates', [])
            existing_dates = {r['date'] if isinstance(r, dict) else r for r in rdates}
            if trading_date not in existing_dates:
                rdates.append({'date': trading_date, 'day_n': day_n_for(existing, trading_date)})
                retrigger_count += 1

    print(f'[EVAL] New entries: {new_count} | Retriggers: {retrigger_count}')
    return new_count, retrigger_count


# ── Baseline logging — all other stocks (for LIFECYCLE) ───────────────────────

def log_baseline(signals, stocks, trading_date):
    """
    Log stocks NOT in today's selected set as tier='baseline'.
    These are tracked for lifecycle analysis but not shown in Stock-Wise.
    Only creates a new entry if the stock has no current OPEN signal.
    """
    open_tickers = {sig.get('ticker', '') for sig in signals if sig.get('outcome') == 'OPEN'}
    cycle_count  = defaultdict(int)
    for sig in signals:
        cycle_count[sig.get('ticker', '')] += 1

    new_count = 0
    for s in stocks:
        ticker = s.get('ticker', '')
        stage  = s.get('stage', 'none')
        if not ticker or stage == 'none':
            continue
        if ticker in open_tickers:
            continue

        cycle = cycle_count[ticker] + 1
        ss    = sig_score(s)
        f     = s.get('fScore', 0) or 0
        t_sc  = rsi_score(s.get('rsi')) + adx_score(s.get('adx'))

        entry = {
            'id':              f'{ticker}_{trading_date}_c{cycle}',
            'ticker':          ticker,
            'name':            s.get('name', ticker),
            'sector':          s.get('sector', 'Others'),
            'board':           s.get('board', 'main'),
            'day0':            trading_date,
            'tier':            'baseline',
            'tier_date':       None,
            'stage':           stage,
            'source':          'eval_worker',
            'score':           f + t_sc + ss,
            'fScore':          f,
            'tScore':          t_sc,
            'sigScore':        ss,
            'rsi':             round(s.get('rsi',  0) or 0, 1),
            'adx':             round(s.get('adx',  0) or 0, 1),
            'pe':              round(s.get('pe',   0) or 0, 1),
            'mcap':            s.get('mcap',        0) or 0,
            'vpbScore':        s.get('vpbScore',    0) or 0,
            'vpbDetail':       s.get('vpbDetail', 'none'),
            'crossScore':      s.get('crossScore',  0) or 0,
            'emaPreCross':     bool(s.get('emaPreCross')),
            'emaCross':        bool(s.get('emaCross')),
            'emaPullback':     bool(s.get('emaPullback')),
            'volConfirm':      bool(s.get('volConfirm')),
            'priceCoiling':    bool(s.get('priceCoiling')),
            'volShrinking':    bool(s.get('volShrinking')),
            'deFlag':          s.get('deFlag'),
            'roeFlag':         s.get('roeFlag'),
            'price_d0':        s.get('price', 0),
            'nifty_d0':        0,
            'prev_stages':     s.get('prevStages', {}),
            'prices':          {trading_date: s.get('price', 0)} if s.get('price') else {},
            'nifty_prices':    {},
            'daily_snapshots': {trading_date: build_daily_snapshot(s, 0)},
            'stage_history':   [{'date': trading_date, 'stage': stage, 'day_n': 0}],
            'retrigger_dates': [],
            'outcome':         'OPEN',
            'outcome_day':     None,
            'outcome_price':   None,
            'outcome_ret':     None,
        }
        signals.append(entry)
        open_tickers.add(ticker)
        cycle_count[ticker] += 1
        new_count += 1

    if new_count:
        print(f'[EVAL] Baseline entries: {new_count}')
    return new_count


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
                sig['nifty_d0'] = sig['nifty_prices'].get(sig.get('day0', ''), round(float(nifty_price), 2))
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


# ── Entry points ──────────────────────────────────────────────────────────────

def run_eod():
    """
    EOD update:
    1. Load stocks + nifty from DB (stocks_live)
    2. Build today's selected set (top-3 per phase + all-tab) using rsi_score/adx_score
    3. For each selected ticker: new entry if no OPEN signal, else retrigger
    4. Log all other non-none stocks as baseline (lifecycle only)
    5. Update prices for all OPEN signals within tracking window
    6. Compute/recompute outcomes (WIN/LOSS/EXPIRED)
    7. Update stage history
    8. Save to DB
    """
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

    # Filter stocks to those with a valid stage for selection
    active_stocks = [s for s in stocks if s.get('stage', 'none') != 'none']

    selected = build_selected(active_stocks)
    print(f'[EVAL] Selected: {len(selected)} stocks for today')

    process_selected(signals, selected, stocks, trading_date)
    log_baseline(signals, stocks, trading_date)
    update_prices(signals, stocks, nifty, mode='eod', trading_date=trading_date)

    # Recompute outcomes after prices are updated
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

    open_count  = sum(1 for s in signals if s.get('outcome') == 'OPEN')
    sig_count   = sum(1 for s in signals if s.get('tier') == 'signal')
    win_count   = sum(1 for s in signals if s.get('outcome') == 'WIN')
    loss_count  = sum(1 for s in signals if s.get('outcome') == 'LOSS')
    exp_count   = sum(1 for s in signals if s.get('outcome') == 'EXPIRED')
    elapsed     = round(_time.time() - _t0, 1)
    print(f'[EVAL] Done — total: {len(signals)} | open: {open_count} | WIN: {win_count} | LOSS: {loss_count} | EXPIRED: {exp_count}')
    _write_status('done', count=sig_count, duration=elapsed,
                  message=f'{sig_count} stockwise · {open_count} open signals')


def run_price_refresh():
    """Intraday price-only update — no new entries, no retriggers, no outcomes."""
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
