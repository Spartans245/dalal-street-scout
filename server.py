"""
Dalal Street Scout - Server v7
================================
HTTP server only — no data fetching, no computation.
All data sourced from data/computed/stocks.json (written by compute.py).

Startup:
  - Loads data/computed/stocks.json if present
  - If missing or stale: auto-spawns orchestrator.py --from kite (assumes NSE data fresh)
  - On first run (no raw data at all): spawns orchestrator.py (full pipeline)

Scheduler:
  - Every 5 min (9:15–3:30 IST Mon–Fri) : kite.ltp() prices + NIFTY + SENSEX
  - 03:30 PM IST Mon–Fri : orchestrator full (NSE fundamentals + Kite scan + compute)
  - Sunday 01:00 AM IST  : full pipeline — NSE universe + fundamentals + Kite + YF + compute
  - File watcher: reloads stocks.json whenever compute.py updates it

API:
  GET /api/stocks              → full stock list + indices
  GET /api/status              → server status
  GET /api/prices              → lightweight price list
  GET /api/stock/<ticker>      → one stock
  GET /api/rescan              → trigger full pipeline
  GET /api/ctrl                → legacy pipeline status JSONs
  GET /api/ctrl/status         → v7 pipeline status + worker PIDs
  GET /api/ctrl/run_prices     → trigger price refresh
  GET /api/refresh/nse         → trigger NSE fundamentals + compute
  GET /api/refresh/kite        → trigger Kite scan + compute
  GET /api/refresh/yf          → trigger YF fundamentals + compute
  GET /api/refresh/compute     → trigger compute only
  GET /api/indices             → NIFTY 50 + SENSEX (direct Kite call)
  GET /api/evals               → signal performance log (reads evals_signals + evals_daily_prices from DB)
  POST /api/evals/log          → manually append a signal entry
  POST /api/analyze/<ticker>   → LLM risk/reward analysis

Double-click START_SERVER.bat to run.
"""

import json, datetime, math, time, threading, os, sys, subprocess
import warnings
warnings.filterwarnings('ignore')

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# Prevent Windows from sleeping while server is running
try:
    import ctypes
    ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)
except Exception:
    pass

from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
from urllib.parse import urlparse, parse_qs
import sqlite3

BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
STATUS_DIR    = os.path.join(BASE_DIR, 'data', 'status')
COMPUTED_FILE = os.path.join(BASE_DIR, 'data', 'computed', 'stocks.json')
SIGNALS_FILE  = os.path.join(BASE_DIR, 'data', 'signals_log.json')
DB_FILE       = os.path.join(BASE_DIR, 'data', 'dalal_street.db')
PYTHON        = sys.executable
PORT          = 5000
LIVE_REFRESH  = 5 * 60   # seconds between price refreshes during market hours

sys.path.insert(0, BASE_DIR)
try:
    from shared.technicals import classify_stage
except Exception:
    def classify_stage(t): return 'none'

import shared.volume_profile as vpmod
from kite_worker import load_instrument_map

VP_CACHE_DIR = os.path.join(BASE_DIR, 'data', 'computed', 'vp_cache')
VP_CACHE_TTL = 6 * 60 * 60  # 6 hours

TOKEN_FILE  = os.path.join(BASE_DIR, 'kite_token.json')
CONFIG_FILE = os.path.join(BASE_DIR, 'kite_config.json')

def load_kite():
    """Return authenticated KiteConnect instance. Raises RuntimeError if token stale."""
    from kiteconnect import KiteConnect
    with open(TOKEN_FILE) as f: tok = json.load(f)
    with open(CONFIG_FILE) as f: cfg = json.load(f)
    today = (datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)).date().isoformat()
    if tok.get('date') != today:
        raise RuntimeError('Kite token stale — run kite_auth.py')
    kite = KiteConnect(api_key=cfg['api_key'])
    kite.set_access_token(tok['access_token'])
    return kite

# ════════════════════════════════════════════════════════════════════
# DB — tab-filtered stock queries
# ════════════════════════════════════════════════════════════════════
VALID_TABS = {'all', 'breakout', 'coiling', 'pre_cross', 'post_cross',
              'pullback', 'trending', 'none'}

# JSON fields stored as text in DB — deserialise on read
_DB_JSON_FIELDS = {'catalysts', 'srResistance', 'srSupport', 'prevStages'}
# Boolean fields stored as 0/1 in DB — convert to bool on read
_DB_BOOL_FIELDS = {
    'near52High', 'macd', 'emaCross', 'emaTrend', 'volConfirm', 'emaPreCross',
    'emaPostCross', 'emaPullback', 'golden', 'priceCoiling', 'volShrinking',
    'ema14Rising', 'ema14RisingFast', 'near38High', 'mmConditional',
}

def _row_to_dict(cur, row):
    """Convert a sqlite3 Row to a plain dict, deserialising JSON/bool fields."""
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

def db_query_stocks(tab=None):
    """Query stocks_live from DB. Returns list of stock dicts, or None on error."""
    if not os.path.exists(DB_FILE):
        return None
    try:
        con = sqlite3.connect(f'file:{DB_FILE}?mode=ro', uri=True,
                              check_same_thread=False)
        if tab and tab in VALID_TABS and tab != 'all':
            cur = con.execute('SELECT * FROM stocks_live WHERE stage=?', (tab,))
        else:
            cur = con.execute('SELECT * FROM stocks_live')
        stocks = [_row_to_dict(cur, row) for row in cur.fetchall()]
        con.close()
        return stocks
    except Exception as e:
        print(f'[DB] stocks query error: {e}')
        return None

def db_query_one(ticker):
    """Query a single stock from stocks_live. Returns dict or None."""
    if not os.path.exists(DB_FILE):
        return None
    try:
        con = sqlite3.connect(f'file:{DB_FILE}?mode=ro', uri=True,
                              check_same_thread=False)
        cur = con.execute('SELECT * FROM stocks_live WHERE ticker=?', (ticker,))
        row = cur.fetchone()
        result = _row_to_dict(cur, row) if row else None
        con.close()
        return result
    except Exception as e:
        print(f'[DB] single stock query error ({ticker}): {e}')
        return None

def db_meta():
    """Return (trading_date, count, stage_counts) from stocks_live, or None on error."""
    if not os.path.exists(DB_FILE):
        return None
    try:
        con = sqlite3.connect(f'file:{DB_FILE}?mode=ro', uri=True,
                              check_same_thread=False)
        cur = con.execute('SELECT trading_date, COUNT(*) FROM stocks_live GROUP BY trading_date')
        row = cur.fetchone()
        if not row:
            con.close()
            return None
        trading_date, count = row
        cur2 = con.execute('SELECT stage, COUNT(*) FROM stocks_live GROUP BY stage')
        stage_counts = {r[0]: r[1] for r in cur2.fetchall()}
        con.close()
        return trading_date, count, stage_counts
    except Exception as e:
        print(f'[DB] meta query error: {e}')
        return None


# ════════════════════════════════════════════════════════════════════
# VOLUME PROFILE — fetch + cache
# ════════════════════════════════════════════════════════════════════
def get_volume_profile(ticker, force_refresh=False, bins=50):
    """Return weekly VP + daily/4H candles + analysis for `ticker`.
    Cached to data/computed/vp_cache/<ticker>.json with a TTL.
    """
    os.makedirs(VP_CACHE_DIR, exist_ok=True)
    cache_file = os.path.join(VP_CACHE_DIR, f'{ticker}.json')

    if not force_refresh and os.path.exists(cache_file):
        age = time.time() - os.path.getmtime(cache_file)
        if age < VP_CACHE_TTL:
            with open(cache_file, encoding='utf-8') as f:
                return json.load(f)

    kite = load_kite()
    token_map = load_instrument_map(kite)
    token = token_map.get(ticker)
    if not token:
        raise RuntimeError(f'No Kite instrument token for {ticker}')

    daily, weekly, fourh = vpmod.fetch_all(kite, token)
    if daily.empty:
        raise RuntimeError(f'No historical data returned for {ticker}')

    vp = vpmod.build_volume_profile(daily, bins=bins)
    analysis = vpmod.build_analysis(daily, weekly, fourh, vp)

    result = {
        'ticker':   ticker,
        'fetched_at': datetime.datetime.now().isoformat(),
        'vp':       vp,
        'analysis': analysis,
        'candles': {
            'daily':  vpmod.candles_to_list(daily, limit=vpmod.DAILY_DISPLAY_BARS),
            'weekly': vpmod.candles_to_list(weekly),
            '4h':     vpmod.candles_to_list(fourh),
        },
    }

    try:
        with open(cache_file, 'w', encoding='utf-8') as f:
            json.dump(result, f)
    except Exception as e:
        print(f'[VP] cache write failed for {ticker}: {e}')

    return result


# ════════════════════════════════════════════════════════════════════
# STATE
# ════════════════════════════════════════════════════════════════════
state = {
    'last_updated':       None,
    'price_refreshed_at': None,
    'status':             'starting',
    'market_mode':        'unknown',
    'count':              0,
    'stage_counts':       {},
    'price_mtime':        0.0,   # unix timestamp of last price refresh — drives 5-min scheduler
    'pipeline_pid':  None,   # orchestrator subprocess PID when running
    'indices':       {},     # {'NIFTY 50': {'price':..,'change':..}, 'SENSEX': {...}}
    'nse_pid':       None,   # nse_worker subprocess PID
    'yf_pid':        None,   # yf_worker subprocess PID
    'compute_pid':   None,   # compute subprocess PID
    'evals_pid':     None,   # eval_worker subprocess PID
    'qa_pid':        None,   # qa_worker subprocess PID
}
state_lock = threading.Lock()


# ════════════════════════════════════════════════════════════════════
# TIME
# ════════════════════════════════════════════════════════════════════
def get_ist():
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) \
           + datetime.timedelta(hours=5, minutes=30)


def get_market_mode():
    d    = get_ist()
    day  = d.weekday()
    mins = d.hour * 60 + d.minute
    if day >= 5:                     return 'weekend'
    if 9*60+15 <= mins < 15*60+30:  return 'open'
    if mins >= 15*60+30:             return 'eod'
    return 'pre'


# ════════════════════════════════════════════════════════════════════
# METADATA LOADER — reads DB meta + status files, no stocks in RAM
# ════════════════════════════════════════════════════════════════════
def load_computed(force=False):
    """Refresh state metadata from DB + status files. No stocks loaded into RAM.
    Returns True if DB has data, False otherwise.
    """
    meta = db_meta()
    if not meta:
        print('[SERVER] Load error: DB unavailable or empty')
        return False

    trading_date, count, stage_counts = meta
    mode = get_market_mode()

    # Price refresh time from status file
    price_refreshed = ''
    try:
        with open(os.path.join(BASE_DIR, 'data', 'status', 'price.json')) as _pf:
            _ps = json.load(_pf)
        if _ps.get('status') == 'done':
            price_refreshed = _ps.get('saved_at', '')
    except Exception:
        pass

    with state_lock:
        state['last_updated']       = trading_date
        state['price_refreshed_at'] = price_refreshed or trading_date
        state['count']              = count
        state['status']             = 'live' if mode == 'open' else 'eod'
        state['market_mode']        = mode
        state['stage_counts']       = stage_counts

    return True


# ════════════════════════════════════════════════════════════════════
# STATUS FILE READER — for /api/ctrl
# ════════════════════════════════════════════════════════════════════
# EVALS — read signals from DB (replaces signals_log.json read)
# ════════════════════════════════════════════════════════════════════
_EVALS_JSON_FIELDS  = {'prev_stages', 'stage_history', 'retrigger_dates'}
_EVALS_BOOL_FIELDS  = {'emaPreCross', 'emaCross', 'emaPullback',
                       'volConfirm', 'priceCoiling', 'volShrinking'}

def read_evals_from_db():
    """Read all signals + daily prices from SQLite. Returns list of signal dicts."""
    try:
        con = sqlite3.connect(DB_FILE)
        con.row_factory = sqlite3.Row

        signals = [dict(r) for r in con.execute('SELECT * FROM evals_signals ORDER BY day0')]

        price_rows = con.execute(
            'SELECT signal_id, trading_date, price, nifty_price FROM evals_daily_prices'
        ).fetchall()
        con.close()
    except Exception as e:
        print(f'[SERVER] read_evals_from_db error: {e}')
        return []

    # Build prices + nifty_prices dicts keyed by signal_id
    prices_map = {}
    nifty_map  = {}
    for row in price_rows:
        sid = row['signal_id']
        if sid not in prices_map:
            prices_map[sid] = {}
            nifty_map[sid]  = {}
        if row['price'] is not None:
            prices_map[sid][row['trading_date']] = row['price']
        if row['nifty_price'] is not None:
            nifty_map[sid][row['trading_date']]  = row['nifty_price']

    result = []
    for s in signals:
        # Deserialize JSON-stored fields
        for f in _EVALS_JSON_FIELDS:
            val = s.get(f)
            if val:
                try:    s[f] = json.loads(val)
                except: s[f] = []
            else:
                s[f] = []
        # Convert SQLite integers back to booleans
        for f in _EVALS_BOOL_FIELDS:
            if s.get(f) is not None:
                s[f] = bool(s[f])
        # Drop daily_snapshots — large, not used by frontend
        s.pop('daily_snapshots', None)
        # Attach prices
        sid = s.get('id', '')
        s['prices']       = prices_map.get(sid, {})
        s['nifty_prices'] = nifty_map.get(sid, {})
        result.append(s)

    return result


# ════════════════════════════════════════════════════════════════════
def read_status_file(name, running_pids=None):
    """Read a worker status file. If status='running' but PID is dead, auto-correct to error.
    running_pids: dict of {pid_key: pid} — pass in to avoid acquiring state_lock here.
    """
    path = os.path.join(STATUS_DIR, f'{name}.json')
    try:
        with open(path) as f:
            data = json.load(f)
    except Exception:
        return {}

    if data.get('status') == 'running' and running_pids is not None:
        pid_key = {
            'nse':          'nse_pid',
            'nse_universe': 'nse_pid',
            'kite':         'pipeline_pid',
            'yf':           'yf_pid',
            'compute':      'compute_pid',
            'evals':        'evals_pid',
            'qa':           'qa_pid',
        }.get(name)
        pid = running_pids.get(pid_key) if pid_key else None
        if not pid_alive(pid):
            data['status']  = 'error'
            data['message'] = (data.get('message') or '') + ' [process no longer running]'
            try:
                with open(path, 'w') as f:
                    json.dump(data, f, indent=2)
            except Exception:
                pass

    return data


# ════════════════════════════════════════════════════════════════════
# SUBPROCESS LAUNCHERS
# ════════════════════════════════════════════════════════════════════
_pipeline_lock = threading.Lock()


def spawn_orchestrator(extra_args=None):
    """Launch orchestrator.py in background. Returns False if already running."""
    with _pipeline_lock:
        with state_lock:
            pid = state.get('pipeline_pid')
        if pid:
            try:
                import psutil
                if psutil.pid_exists(pid):
                    return False  # already running
            except ImportError:
                pass  # psutil not available — allow re-launch

        cmd = [PYTHON, '-W', 'ignore', os.path.join(BASE_DIR, 'orchestrator.py'), '--eod']
        if extra_args:
            cmd += extra_args
        print(f'[SERVER] Spawning: {" ".join(cmd)}')

        def _run():
            proc = subprocess.Popen(cmd, cwd=BASE_DIR,
                creationflags=subprocess.BELOW_NORMAL_PRIORITY_CLASS)
            with state_lock:
                state['pipeline_pid'] = proc.pid
                state['status']       = 'scanning'
            proc.wait()
            with state_lock:
                state['pipeline_pid'] = None
            # Reload data after pipeline finishes
            load_computed(force=True)
            mode = get_market_mode()
            with state_lock:
                state['status'] = 'live' if mode == 'open' else 'eod'
            print(f'[SERVER] Pipeline done — stocks reloaded')

        threading.Thread(target=_run, daemon=True).start()
    return True


_price_refresh_running = False
_price_refresh_lock    = threading.Lock()

def spawn_price_refresh():
    """Launch kite_worker.py --price-refresh in background. No-op if already running."""
    global _price_refresh_running
    with _price_refresh_lock:
        if _price_refresh_running:
            return  # already in flight
        _price_refresh_running = True
    cmd = [PYTHON, '-W', 'ignore',
           os.path.join(BASE_DIR, 'kite_worker.py'), '--price-refresh']
    print(f'[SERVER] Spawning price refresh...')

    def _run():
        global _price_refresh_running
        try:
            proc = subprocess.Popen(cmd, cwd=BASE_DIR,
                creationflags=subprocess.BELOW_NORMAL_PRIORITY_CLASS)
            proc.wait()
            if proc.returncode == 0:
                load_computed()
                with state_lock:
                    state['price_mtime'] = time.time()
        finally:
            with _price_refresh_lock:
                _price_refresh_running = False

    threading.Thread(target=_run, daemon=True).start()


def pid_alive(pid):
    """Return True if a process with this PID is still running."""
    if not pid:
        return False
    try:
        import psutil
        return psutil.pid_exists(pid)
    except ImportError:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

def spawn_nse_worker(then_compute=True):
    """Launch nse_worker.py --fundamentals in background. Optionally re-runs compute after."""
    with state_lock:
        pid = state.get('nse_pid')
        if pid and pid_alive(pid):
            return False  # already running
        elif pid:
            state['nse_pid'] = None  # stale PID — clear it
    cmd = [PYTHON, '-W', 'ignore', os.path.join(BASE_DIR, 'nse_worker.py'), '--fundamentals']
    print('[SERVER] Spawning NSE fundamentals...')
    def _run():
        proc = subprocess.Popen(cmd, cwd=BASE_DIR,
            creationflags=subprocess.BELOW_NORMAL_PRIORITY_CLASS)
        with state_lock: state['nse_pid'] = proc.pid
        proc.wait()
        with state_lock: state['nse_pid'] = None
        if proc.returncode == 0 and then_compute:
            print('[SERVER] NSE done — re-running compute...')
            spawn_compute()
    threading.Thread(target=_run, daemon=True).start()
    return True


def spawn_yf_worker():
    """Launch yf_worker.py --fundamentals in background."""
    with state_lock:
        if state.get('yf_pid'):
            return False
    cmd = [PYTHON, '-W', 'ignore', os.path.join(BASE_DIR, 'yf_worker.py'), '--fundamentals']
    print('[SERVER] Spawning YF fundamentals...')
    def _run():
        proc = subprocess.Popen(cmd, cwd=BASE_DIR,
            creationflags=subprocess.BELOW_NORMAL_PRIORITY_CLASS)
        with state_lock: state['yf_pid'] = proc.pid
        proc.wait()
        with state_lock: state['yf_pid'] = None
        if proc.returncode == 0:
            print('[SERVER] YF done — re-running compute...')
            spawn_compute()
    threading.Thread(target=_run, daemon=True).start()
    return True


def spawn_compute():
    """Launch compute.py standalone in background."""
    with state_lock:
        if state.get('compute_pid'):
            return False
    cmd = [PYTHON, '-W', 'ignore', os.path.join(BASE_DIR, 'compute.py')]
    print('[SERVER] Spawning compute...')
    def _run():
        proc = subprocess.Popen(cmd, cwd=BASE_DIR,
            creationflags=subprocess.BELOW_NORMAL_PRIORITY_CLASS)
        with state_lock: state['compute_pid'] = proc.pid
        proc.wait()
        with state_lock: state['compute_pid'] = None
        if proc.returncode == 0:
            db_cmd = [PYTHON, '-W', 'ignore', os.path.join(BASE_DIR, 'db_writer.py')]
            db_proc = subprocess.Popen(db_cmd, cwd=BASE_DIR,
                creationflags=subprocess.BELOW_NORMAL_PRIORITY_CLASS)
            db_proc.wait()
            if db_proc.returncode == 0:
                print('[SERVER] db_writer done — stocks_master updated')
            else:
                print(f'[SERVER] WARN: db_writer exited {db_proc.returncode}')
    threading.Thread(target=_run, daemon=True).start()
    return True


_evals_lock = threading.Lock()

def spawn_evals_worker(force=False):
    """Launch eval_worker.py --eod in background. Returns False if already running."""
    with _evals_lock:
        with state_lock:
            if state.get('evals_pid'):
                return False  # already running
        cmd = [PYTHON, '-W', 'ignore',
               os.path.join(BASE_DIR, 'eval_worker.py'), '--eod']
        if force:
            cmd.append('--force')
        print(f'[SERVER] Spawning eval_worker: {" ".join(cmd)}')

        _evals_status_path = os.path.join(BASE_DIR, 'data', 'status', 'evals.json')

        def _write_evals_status(status, count=None, errors=0, duration=None, message=''):
            obj = {
                'status':   status,
                'saved_at': datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat(),
                'count':    count,
                'errors':   errors,
                'duration': duration,
                'message':  message,
            }
            try:
                with open(_evals_status_path, 'w', encoding='utf-8') as f:
                    json.dump(obj, f)
            except Exception as e:
                print(f'[SERVER] Could not write evals status: {e}')

        def _run():
            import time as _t
            t0 = _t.time()
            _write_evals_status('running')
            proc = subprocess.Popen(cmd, cwd=BASE_DIR,
                creationflags=subprocess.BELOW_NORMAL_PRIORITY_CLASS)
            with state_lock:
                state['evals_pid'] = proc.pid
            proc.wait()
            elapsed = round(_t.time() - t0, 1)
            with state_lock:
                state['evals_pid'] = None
            if proc.returncode == 0:
                print(f'[SERVER] eval_worker done ({elapsed}s)')
                # Run analytics → lifecycle → evals_db_writer → qa_worker in sequence
                for label, script in [
                    ('analytics_worker', 'analytics_worker.py'),
                    ('lifecycle_worker', 'lifecycle_worker.py'),
                    ('evals_db_writer',  'evals_db_writer.py'),
                    ('qa_worker',        'qa_worker.py'),
                ]:
                    w = subprocess.Popen(
                        [PYTHON, '-W', 'ignore', os.path.join(BASE_DIR, script)],
                        cwd=BASE_DIR,
                        creationflags=subprocess.BELOW_NORMAL_PRIORITY_CLASS,
                    )
                    w.wait()
                    if w.returncode != 0:
                        print(f'[SERVER] WARN: {label} exited {w.returncode}')
                    else:
                        print(f'[SERVER] {label} done')
            else:
                _write_evals_status('error', errors=1, duration=elapsed,
                                    message=f'exit code {proc.returncode}')
                print(f'[SERVER] eval_worker exited with code {proc.returncode}')

        threading.Thread(target=_run, daemon=True).start()
    return True


_qa_lock = threading.Lock()

def spawn_qa_worker():
    """Launch qa_worker.py in background. Returns False if already running."""
    with _qa_lock:
        with state_lock:
            if state.get('qa_pid'):
                return False
        cmd = [PYTHON, '-W', 'ignore', os.path.join(BASE_DIR, 'qa_worker.py')]

        def _run():
            import time as _t
            t0 = _t.time()
            proc = subprocess.Popen(cmd, cwd=BASE_DIR,
                creationflags=subprocess.BELOW_NORMAL_PRIORITY_CLASS)
            with state_lock:
                state['qa_pid'] = proc.pid
            proc.wait()
            with state_lock:
                state['qa_pid'] = None
            elapsed = round(_t.time() - t0, 1)
            if proc.returncode == 0:
                print(f'[SERVER] qa_worker done ({elapsed}s)')
            else:
                print(f'[SERVER] qa_worker exited with code {proc.returncode}')

        threading.Thread(target=_run, daemon=True).start()
    return True


# ════════════════════════════════════════════════════════════════════
# SCHEDULER
# ════════════════════════════════════════════════════════════════════
def scheduler():
    print('[SERVER] Scheduler starting...')

    # Load metadata from DB on startup
    loaded = load_computed()

    if not loaded:
        print('[SERVER] No computed data — starting full pipeline...')
        with state_lock:
            state['status'] = 'scanning'
        spawn_orchestrator()
    else:
        mode = get_market_mode()
        if mode == 'open':
            spawn_price_refresh()

    # Initialize flags from status files so restarts don't re-trigger completed scans
    _today = get_ist().date().isoformat()
    try:
        with open(os.path.join(BASE_DIR, 'data', 'status', 'compute.json')) as _f:
            _cs = json.load(_f)
        _computed_today = _cs.get('status') == 'done' and (_cs.get('saved_at','') or '')[:10] == _today
    except Exception:
        _computed_today = False
    eod_done_today        = _computed_today
    sunday_done_this_week = False

    while True:
        time.sleep(30)  # check every 30 seconds
        now  = get_ist()
        mode = get_market_mode()
        day  = now.weekday()   # 0=Mon … 6=Sun
        mins = now.hour * 60 + now.minute

        # Refresh metadata from DB every 30s tick
        load_computed()

        # ── MARKET OPEN: price refresh every 5 min ──────────────────
        if mode == 'open':
            eod_done_today = False

            # Price refresh every 5 min — skip if full scan already running
            with state_lock:
                busy       = bool(state.get('pipeline_pid') or state.get('nse_pid') or state.get('compute_pid'))
                price_mtime = state['price_mtime']
            if not busy and (time.time() - price_mtime) > LIVE_REFRESH:
                spawn_price_refresh()

        # ── EOD: owned by REFRESH_EOD.bat (Task Scheduler 3:50 PM) ──────
        # Server no longer triggers EOD — REFRESH_EOD.bat is the single owner.
        # Dual-trigger caused simultaneous orchestrator runs → server crash loop.
        elif mode == 'eod':
            pass  # nothing to reset

        # ── SUNDAY 1 AM: Full scan — NSE universe + fundamentals + Kite + YF + compute ──
        if day == 6 and 1*60 <= mins < 1*60+30 and not sunday_done_this_week:
            print('[SCHEDULER] Sunday 1 AM: triggering full pipeline (NSE universe + Kite + YF + compute)...')
            sunday_done_this_week = True
            spawn_orchestrator()   # full: universe → NSE fundamentals + Kite (parallel) + YF → compute
        elif day == 0:  # Monday resets the flag
            sunday_done_this_week = False


# ════════════════════════════════════════════════════════════════════
# JSON HELPER
# ════════════════════════════════════════════════════════════════════
def _safe_json(data):
    raw = json.dumps(data, ensure_ascii=False)
    return raw.replace('Infinity', 'null').replace('NaN', 'null')


# ════════════════════════════════════════════════════════════════════
# HTTP HANDLER
# ════════════════════════════════════════════════════════════════════
class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args): pass  # suppress request logs

    def handle_error(self, request, client_address):
        pass  # suppress ConnectionAbortedError / BrokenPipe noise in console

    def send_json(self, data, status=200):
        body = _safe_json(data).encode('utf-8')
        try:
            self.send_response(status)
            self.send_header('Content-Type',   'application/json')
            self.send_header('Content-Length', len(body))
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionAbortedError, BrokenPipeError, OSError):
            pass

    def send_file(self, fname, ctype):
        path = os.path.join(BASE_DIR, fname)
        try:
            with open(path, 'rb') as f:
                body = f.read()
            self.send_response(200)
            self.send_header('Content-Type',   ctype)
            self.send_header('Content-Length', len(body))
            if fname.endswith('.html'):
                self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
            self.end_headers()
            self.wfile.write(body)
        except FileNotFoundError:
            self.send_response(404); self.end_headers()
        except (ConnectionAbortedError, BrokenPipeError, OSError):
            pass

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_POST(self):
        path = urlparse(self.path).path

        # ── EVALS — manual signal log ────────────────────────────────
        if path == '/api/evals/log':
            try:
                length = int(self.headers.get('Content-Length', 0))
                body   = self.rfile.read(length)
                entry  = json.loads(body)
                if os.path.exists(SIGNALS_FILE):
                    with open(SIGNALS_FILE, encoding='utf-8') as f:
                        data = json.load(f)
                else:
                    data = {'signals': []}
                entry['source'] = 'manual'
                d0 = entry.get('day0', '')
                entry.setdefault('prices', {d0: entry.get('price_d0', 0)} if d0 else {})
                data['signals'].append(entry)
                with open(SIGNALS_FILE, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                self.send_json({'ok': True, 'count': len(data['signals'])})
            except Exception as e:
                self.send_json({'ok': False, 'error': str(e)}, 500)
            return

        # ── EVALS — manual EOD compute trigger ───────────────────────
        if path == '/api/evals/compute':
            try:
                result = subprocess.run(
                    [sys.executable, os.path.join(BASE_DIR, 'eval_worker.py'), '--eod'],
                    capture_output=True, text=True, timeout=120,
                    encoding='utf-8', errors='replace'
                )
                ok = result.returncode == 0
                self.send_json({
                    'ok':     ok,
                    'stdout': result.stdout[-3000:] if result.stdout else '',
                    'stderr': result.stderr[-1000:] if result.stderr else '',
                })
            except subprocess.TimeoutExpired:
                self.send_json({'ok': False, 'error': 'eval_worker timed out (>120s)'}, 500)
            except Exception as e:
                self.send_json({'ok': False, 'error': str(e)}, 500)
            return

        # ── LLM Risk/Reward analysis for one stock ───────────────────
        if path.startswith('/api/analyze/'):
            ticker = path.replace('/api/analyze/', '').upper().strip()
            stock = db_query_one(ticker)
            if not stock:
                self.send_json({'error': f'{ticker} not found'}, 404)
                return
            try:
                from agents.sr_analysis_agent import analyze
                result = analyze(stock)
                self.send_json(result)
            except Exception as e:
                print(f'[ANALYZE] Error for {ticker}: {e}')
                self.send_json({'error': str(e)}, 500)
            return

        # ── Kite auth — exchange request_token → access_token ────────
        if path == '/api/kite/auth':
            try:
                length = int(self.headers.get('Content-Length', 0))
                body   = json.loads(self.rfile.read(length))
                request_token = body.get('request_token', '').strip()
                if not request_token:
                    self.send_json({'ok': False, 'error': 'request_token missing'}, 400)
                    return
                cfg_path = os.path.join(BASE_DIR, 'kite_config.json')
                tok_path = os.path.join(BASE_DIR, 'kite_token.json')
                with open(cfg_path) as f:
                    cfg = json.load(f)
                from kiteconnect import KiteConnect
                kite = KiteConnect(api_key=cfg['api_key'])
                data = kite.generate_session(request_token, api_secret=cfg['api_secret'])
                token_data = {
                    'access_token': data['access_token'],
                    'date':         datetime.date.today().isoformat(),
                    'user_id':      data.get('user_id', ''),
                    'user_name':    data.get('user_name', ''),
                    'generated_at': datetime.datetime.now().isoformat(),
                }
                with open(tok_path, 'w') as f:
                    json.dump(token_data, f, indent=2)
                print(f'[KITE] Auth success: {data.get("user_name")} ({data.get("user_id")})')
                self.send_json({'ok': True, 'user': data.get('user_name', ''), 'user_id': data.get('user_id', '')})
            except Exception as e:
                self.send_json({'ok': False, 'error': str(e)}, 500)
            return

        self.send_response(404); self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path

        # ── Static files ────────────────────────────────────────────
        if path in ('/', '/index.html'):
            self.send_file('index.html', 'text/html; charset=utf-8')
            return
        if path == '/architecture.html':
            self.send_file('architecture.html', 'text/html; charset=utf-8')
            return
        if path == '/architecture_db.html':
            self.send_file('architecture_db.html', 'text/html; charset=utf-8')
            return
        if path == '/metrics.html':
            self.send_file('metrics.html', 'text/html; charset=utf-8')
            return

        # ── Status ──────────────────────────────────────────────────
        if path == '/api/status':
            with state_lock:
                data = {
                    'status':         state['status'],
                    'market_mode':    state['market_mode'],
                    'last_updated':   state['last_updated'],
                    'count':          state['count'],
                    'ist_time':           get_ist().strftime('%H:%M:%S'),
                    'price_refreshed_at': state['price_refreshed_at'],
                    'fetch_progress': 100 if state['status'] not in ('scanning','starting') else 50,
                    'fetch_message':  state['status'],
                    'total_scanned':  state['count'],
                    'in_range':       state['count'],
                }
            self.send_json(data)
            return

        # ── Stocks ──────────────────────────────────────────────────
        if path == '/api/stocks':
            qs  = parse_qs(urlparse(self.path).query)
            tab = qs.get('tab', [None])[0]

            with state_lock:
                base = {
                    'status':             state['status'],
                    'market_mode':        state['market_mode'],
                    'last_updated':       state['last_updated'],
                    'price_refreshed_at': state['price_refreshed_at'],
                    'indices':            dict(state['indices']),
                    'stage_counts':       dict(state['stage_counts']),
                    'total_count':        state['count'],
                }

            stocks = db_query_stocks(tab if tab in VALID_TABS else None)
            if stocks is None:
                self.send_json({'error': 'DB unavailable'}, 503)
                return
            if tab and tab in VALID_TABS and tab != 'all':
                base['tab'] = tab

            self.send_json({**base, 'stocks': stocks})
            return

        # ── Prices (lightweight) ────────────────────────────────────
        if path == '/api/prices':
            stocks = db_query_stocks()
            if stocks is None:
                self.send_json({'error': 'DB unavailable'}, 503)
                return
            prices = [
                {'ticker': s['ticker'], 'price': s['price'], 'change': s['change']}
                for s in stocks
            ]
            with state_lock:
                status       = state['status']
                last_updated = state['last_updated']
            self.send_json({'status': status, 'last_updated': last_updated, 'prices': prices})
            return

        # ── Single stock ────────────────────────────────────────────
        if path.startswith('/api/stock/'):
            ticker = path.replace('/api/stock/', '').upper().strip()
            stock = db_query_one(ticker)
            self.send_json(stock if stock else {'error': 'Not found'}, 200 if stock else 404)
            return

        # ── Rescan — spawn full pipeline ────────────────────────────
        if path == '/api/rescan':
            with state_lock:
                busy = state['status'] == 'scanning'
            if busy:
                self.send_json({'ok': False, 'msg': 'Scan already running'})
            else:
                ok = spawn_orchestrator()
                self.send_json({'ok': ok, 'msg': 'Full pipeline started' if ok else 'Already running'})
            return

        # ── Ctrl dashboard ──────────────────────────────────────────
        if path == '/api/ctrl':
            with state_lock:
                running_pids = {
                    'pipeline_pid': state.get('pipeline_pid'),
                    'nse_pid':      state.get('nse_pid'),
                    'yf_pid':       state.get('yf_pid'),
                    'compute_pid':  state.get('compute_pid'),
                    'evals_pid':    state.get('evals_pid'),
                }
            ctrl = {
                'nse':     read_status_file('nse',     running_pids),
                'kite':    read_status_file('kite',    running_pids),
                'yf':      read_status_file('yf',      running_pids),
                'compute': read_status_file('compute', running_pids),
            }
            with state_lock:
                ctrl['pipeline_pid'] = state.get('pipeline_pid')
            self.send_json(ctrl)
            return

        # ── Manual price refresh ────────────────────────────────────
        if path == '/api/ctrl/run_prices':
            with state_lock:
                no_stocks = state['count'] == 0
            if no_stocks:
                self.send_json({'ok': False, 'msg': 'No stocks loaded yet'})
            else:
                spawn_price_refresh()
                self.send_json({'ok': True, 'msg': 'Price refresh started'})
            return

        # ── Manual refresh endpoints ────────────────────────────────
        if path == '/api/refresh/nse':
            ok = spawn_nse_worker(then_compute=True)
            self.send_json({'ok': ok, 'msg': 'NSE fundamentals started' if ok else 'Already running'})
            return

        if path == '/api/refresh/kite':
            with state_lock:
                busy = bool(state.get('pipeline_pid'))
            if busy:
                self.send_json({'ok': False, 'msg': 'Pipeline already running'})
            else:
                ok = spawn_orchestrator(['--from', 'kite'])
                self.send_json({'ok': ok, 'msg': 'Kite scan + compute started' if ok else 'Already running'})
            return

        if path == '/api/evals/run':
            with state_lock:
                already = bool(state.get('evals_pid'))
            if already:
                self.send_json({'ok': False, 'msg': 'EVALS worker already running'})
            else:
                ok = spawn_evals_worker(force=True)
                self.send_json({'ok': ok, 'msg': 'EVALS worker started' if ok else 'Already running'})
            return

        if path == '/api/qa/run':
            with state_lock:
                already = bool(state.get('qa_pid'))
            if already:
                self.send_json({'ok': False, 'msg': 'QA worker already running'})
            else:
                ok = spawn_qa_worker()
                self.send_json({'ok': ok, 'msg': 'QA worker started' if ok else 'Already running'})
            return

        if path == '/api/refresh/yf':
            ok = spawn_yf_worker()
            self.send_json({'ok': ok, 'msg': 'YF fundamentals started' if ok else 'Already running'})
            return

        if path == '/api/refresh/compute':
            ok = spawn_compute()
            self.send_json({'ok': ok, 'msg': 'Compute started' if ok else 'Already running'})
            return

        # ── Ctrl status — include all worker PIDs ───────────────────
        if path == '/api/ctrl/status':
            with state_lock:
                self.send_json({
                    'pipeline_pid':  state.get('pipeline_pid'),
                    'nse_pid':       state.get('nse_pid'),
                    'yf_pid':        state.get('yf_pid'),
                    'compute_pid':   state.get('compute_pid'),
                    'evals_pid':     state.get('evals_pid'),
                    'qa_pid':        state.get('qa_pid'),
                    'nse_universe':  read_status_file('nse_universe'),
                    'nse':           read_status_file('nse'),
                    'kite':          read_status_file('kite'),
                    'price':         read_status_file('price'),
                    'yf':            read_status_file('yf'),
                    'compute':       read_status_file('compute'),
                    'evals':         read_status_file('evals'),
                    'qa':            read_status_file('qa'),
                })
            return

        # ── Kite auth — open browser on server machine ──────────────
        if path == '/api/kite/open_browser':
            try:
                import webbrowser
                cfg_path = os.path.join(BASE_DIR, 'kite_config.json')
                with open(cfg_path) as f:
                    cfg = json.load(f)
                from kiteconnect import KiteConnect
                kite = KiteConnect(api_key=cfg['api_key'])
                login_url = kite.login_url()
                webbrowser.open(login_url)
                print(f'[KITE] Opened browser: {login_url}')
                self.send_json({'ok': True, 'url': login_url})
            except Exception as e:
                self.send_json({'ok': False, 'error': str(e)})
            return

        # ── Kite auth — login URL + token status ────────────────────
        if path == '/api/kite/status':
            try:
                cfg_path = os.path.join(BASE_DIR, 'kite_config.json')
                tok_path = os.path.join(BASE_DIR, 'kite_token.json')
                with open(cfg_path) as f:
                    cfg = json.load(f)
                api_key = cfg.get('api_key', '')
                from kiteconnect import KiteConnect
                kite = KiteConnect(api_key=api_key)
                login_url = kite.login_url()
                token_info = {'valid': False, 'date': '', 'user': ''}
                if os.path.exists(tok_path):
                    with open(tok_path) as f:
                        tok = json.load(f)
                    today = datetime.date.today().isoformat()
                    token_info = {
                        'valid':   tok.get('date') == today and bool(tok.get('access_token')),
                        'date':    tok.get('date', ''),
                        'user':    tok.get('user_name', ''),
                        'user_id': tok.get('user_id', ''),
                    }
                self.send_json({'ok': True, 'login_url': login_url, 'token': token_info})
            except Exception as e:
                self.send_json({'ok': False, 'error': str(e)})
            return

        # ── Indices — NIFTY 50 + SENSEX via Kite ltp() ─────────────
        if path == '/api/indices':
            try:
                kite   = load_kite()
                keys   = ['NSE:NIFTY 50', 'BSE:SENSEX']
                data   = kite.quote(keys)   # quote() returns ohlc.close = prev day close
                result = {}
                for key, label in [('NSE:NIFTY 50', 'NIFTY 50'), ('BSE:SENSEX', 'SENSEX')]:
                    entry = data.get(key, {})
                    last  = round(float(entry.get('last_price') or 0), 2)
                    prev  = round(float((entry.get('ohlc') or {}).get('close') or last), 2)
                    chg   = round((last - prev) / prev * 100, 2) if prev else 0
                    result[label] = {'price': last, 'change': chg}
                self.send_json(result)
            except Exception as e:
                try:
                    self.send_json({'error': str(e)})
                except Exception:
                    pass  # browser already closed connection
            return

        # ── EVALS — signal performance log (reads from SQLite DB) ───
        if path == '/api/evals':
            try:
                signals = read_evals_from_db()
                self.send_json({'signals': signals})
            except Exception as e:
                self.send_json({'signals': [], 'error': str(e)})
            return

        # ── QA log ────────────────────────────────────────────────────
        if path == '/api/qa':
            qa_log = os.path.join(BASE_DIR, 'data', 'qa_log.json')
            try:
                if os.path.exists(qa_log):
                    with open(qa_log, encoding='utf-8') as f:
                        data = json.load(f)
                else:
                    data = []
                self.send_json(data)
            except Exception as e:
                self.send_json({'error': str(e)})
            return

        # ── LIFECYCLE — all-stock journey tracker ─────────────────────
        if path == '/api/lifecycle':
            lf = os.path.join(BASE_DIR, 'data', 'lifecycle_log.json')
            try:
                if os.path.exists(lf):
                    with open(lf, encoding='utf-8') as f:
                        data = json.load(f)
                else:
                    data = {'version': 1, 'stocks': {}}
                self.send_json(data)
            except Exception as e:
                self.send_json({'version': 1, 'stocks': {}, 'error': str(e)})
            return

        # ── EVALS — pre-computed analytics ───────────────────────────
        if path == '/api/evals/analytics':
            analytics_file = os.path.join(BASE_DIR, 'data', 'evals', 'analytics.json')
            try:
                if os.path.exists(analytics_file):
                    with open(analytics_file, encoding='utf-8') as f:
                        data = json.load(f)
                else:
                    data = {}
                self.send_json(data)
            except Exception as e:
                self.send_json({'error': str(e)})
            return

        if path == '/api/regression':
            reg_file = os.path.join(BASE_DIR, 'data', 'evals', 'regression.json')
            try:
                if os.path.exists(reg_file):
                    with open(reg_file, encoding='utf-8') as f:
                        data = json.load(f)
                else:
                    data = {'n_resolved': 0, 'min_needed': 5, 'features': [], 'combinations': [], 'rsi_adx_heatmap': [], 'score_bands': []}
                self.send_json(data)
            except Exception as e:
                self.send_json({'error': str(e)})
            return

        # ── Volume Profile analyser ──────────────────────────────────
        if path.startswith('/api/volume-profile/'):
            ticker = path.split('/')[-1].upper().strip()
            qs = parse_qs(urlparse(self.path).query)
            force_refresh = qs.get('refresh', ['0'])[0] == '1'
            try:
                data = get_volume_profile(ticker, force_refresh=force_refresh)
                self.send_json(data)
            except Exception as e:
                self.send_json({'error': str(e)}, status=500)
            return

        self.send_response(404); self.end_headers()


# ════════════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════════════
def main():
    print(f"""
==========================================
  DALAL STREET SCOUT  v7  Local Server
==========================================
  Browser -> http://localhost:{PORT}
  Press Ctrl+C to stop
""")

    # Bind port first — exit immediately if another server is already running.
    # This prevents duplicate scheduler threads from spawning price refreshes.
    try:
        server = ThreadingHTTPServer(('0.0.0.0', PORT), Handler)
    except OSError:
        print(f'\n  Port {PORT} is already in use — another server is running.')
        print(f'  This instance will not start.')
        sys.exit(0)   # exit 0 so START_SERVER.bat restart loop doesn't trigger

    threading.Thread(target=scheduler, daemon=True).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n  Server stopped.')


if __name__ == '__main__':
    main()
