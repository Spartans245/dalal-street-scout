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
  - 12:00 PM IST Mon–Fri : orchestrator --from kite (Kite scan + full compute: technicals + stages + scores)
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
from urllib.parse import urlparse

BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
STATUS_DIR    = os.path.join(BASE_DIR, 'data', 'status')
COMPUTED_FILE = os.path.join(BASE_DIR, 'data', 'computed', 'stocks.json')
PYTHON        = sys.executable
PORT          = 5000
LIVE_REFRESH  = 5 * 60   # seconds between price refreshes during market hours

sys.path.insert(0, BASE_DIR)
try:
    from shared.technicals import classify_stage
except Exception:
    def classify_stage(t): return 'none'

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
# STATE
# ════════════════════════════════════════════════════════════════════
state = {
    'stocks':        [],
    'last_updated':  None,
    'status':        'starting',
    'market_mode':   'unknown',
    'count':         0,
    'file_mtime':    0.0,
    'pipeline_pid':  None,   # orchestrator subprocess PID when running
    'indices':       {},     # {'NIFTY 50': {'price':..,'change':..}, 'SENSEX': {...}}
    'nse_pid':       None,   # nse_worker subprocess PID
    'yf_pid':        None,   # yf_worker subprocess PID
    'compute_pid':   None,   # compute subprocess PID
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
# COMPUTED FILE LOADER
# ════════════════════════════════════════════════════════════════════
def load_computed(force=False):
    """Load data/computed/stocks.json into state. Thread-safe.
    Returns True on success, False on failure.
    """
    if not os.path.exists(COMPUTED_FILE):
        return False
    try:
        mtime = os.path.getmtime(COMPUTED_FILE)
        with state_lock:
            current_mtime = state['file_mtime']
        if not force and mtime <= current_mtime:
            return True  # no change

        with open(COMPUTED_FILE, encoding='utf-8') as f:
            data = json.load(f)

        stocks = data.get('stocks', [])
        if not stocks:
            return False

        # Re-classify stages from stored tech fields (picks up any classify_stage() changes)
        for s in stocks:
            try:
                tech = {
                    'ema_cross':           s.get('emaCross', False),
                    'ema_cross_days_ago':  s.get('emaCrossDays'),
                    'ema_trend':           s.get('emaTrend', False),
                    'ema_pre_cross':       s.get('emaPreCross', False),
                    'ema_post_cross':      s.get('emaPostCross', False),
                    'ema_pullback':        s.get('emaPullback', False),
                    'vol_confirmed_cross': s.get('volConfirm', False),
                    'vpb_detail':          s.get('vpbDetail', 'none'),
                    'vpb_score':           s.get('vpbScore', 0),
                }
                s['stage'] = classify_stage(tech)
            except Exception:
                pass

        saved_at  = data.get('saved_at', '')
        mode      = get_market_mode()

        indices = data.get('indices', {})

        with state_lock:
            state['stocks']       = stocks
            state['last_updated'] = saved_at
            state['count']        = len(stocks)
            state['status']       = 'live' if mode == 'open' else 'eod'
            state['market_mode']  = mode
            state['file_mtime']   = mtime
            if indices:
                state['indices']  = indices

        print(f'[SERVER] Loaded {len(stocks)} stocks from computed (saved {saved_at[:16]})')
        return True

    except Exception as e:
        print(f'[SERVER] Load error: {e}')
        return False


# ════════════════════════════════════════════════════════════════════
# STATUS FILE READER — for /api/ctrl
# ════════════════════════════════════════════════════════════════════
def read_status_file(name):
    path = os.path.join(STATUS_DIR, f'{name}.json')
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


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

        cmd = [PYTHON, '-W', 'ignore', os.path.join(BASE_DIR, 'orchestrator.py')]
        if extra_args:
            cmd += extra_args
        print(f'[SERVER] Spawning: {" ".join(cmd)}')

        def _run():
            proc = subprocess.Popen(cmd, cwd=BASE_DIR)
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


def spawn_price_refresh():
    """Launch kite_worker.py --price-refresh in background."""
    cmd = [PYTHON, '-W', 'ignore',
           os.path.join(BASE_DIR, 'kite_worker.py'), '--price-refresh']
    print(f'[SERVER] Spawning price refresh...')

    def _run():
        proc = subprocess.Popen(cmd, cwd=BASE_DIR)
        proc.wait()
        if proc.returncode == 0:
            load_computed(force=True)

    threading.Thread(target=_run, daemon=True).start()


def spawn_nse_worker(then_compute=True):
    """Launch nse_worker.py --fundamentals in background. Optionally re-runs compute after."""
    with state_lock:
        if state.get('nse_pid'):
            return False  # already running
    cmd = [PYTHON, '-W', 'ignore', os.path.join(BASE_DIR, 'nse_worker.py'), '--fundamentals']
    print('[SERVER] Spawning NSE fundamentals...')
    def _run():
        proc = subprocess.Popen(cmd, cwd=BASE_DIR)
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
        proc = subprocess.Popen(cmd, cwd=BASE_DIR)
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
        proc = subprocess.Popen(cmd, cwd=BASE_DIR)
        with state_lock: state['compute_pid'] = proc.pid
        proc.wait()
        with state_lock: state['compute_pid'] = None
        if proc.returncode == 0:
            load_computed(force=True)
            print('[SERVER] Compute done — stocks reloaded')
    threading.Thread(target=_run, daemon=True).start()
    return True


# ════════════════════════════════════════════════════════════════════
# SCHEDULER
# ════════════════════════════════════════════════════════════════════
def scheduler():
    print('[SERVER] Scheduler starting...')

    # Load computed stocks on startup
    loaded = load_computed(force=True)

    if not loaded:
        print('[SERVER] No computed data — starting full pipeline...')
        with state_lock:
            state['status'] = 'scanning'
        spawn_orchestrator()
    else:
        mode = get_market_mode()
        if mode == 'open':
            spawn_price_refresh()

    eod_done_today     = False
    midday_done_today  = False
    sunday_done_this_week = False  # Sunday 1 AM full scan

    while True:
        time.sleep(30)  # check every 30 seconds
        now  = get_ist()
        mode = get_market_mode()
        day  = now.weekday()   # 0=Mon … 6=Sun
        mins = now.hour * 60 + now.minute

        # File watcher — reload if compute.py updated stocks.json
        load_computed()

        # ── MARKET OPEN: price refresh every 5 min ──────────────────
        if mode == 'open':
            eod_done_today = False

            # 12:00 PM midday: full Kite scan + compute (technicals + stages)
            if 12*60 <= mins < 12*60+30 and not midday_done_today:
                print('[SCHEDULER] Midday: triggering Kite scan + compute...')
                midday_done_today = True
                spawn_orchestrator(['--from', 'kite'])
            else:
                # Price refresh every 5 min
                with state_lock:
                    mtime = state['file_mtime']
                try:
                    if time.time() - mtime > LIVE_REFRESH:
                        spawn_price_refresh()
                except Exception:
                    pass

        # ── EOD: Full pipeline — NSE fundamentals + Kite scan + compute ──
        elif mode == 'eod' and not eod_done_today:
            print('[SCHEDULER] EOD: triggering full pipeline (NSE + Kite + compute)...')
            eod_done_today    = True
            midday_done_today = False  # reset for tomorrow
            spawn_orchestrator()       # full: NSE fundamentals + Kite scan + compute

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
        self.send_response(status)
        self.send_header('Content-Type',   'application/json')
        self.send_header('Content-Length', len(body))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, fname, ctype):
        path = os.path.join(BASE_DIR, fname)
        try:
            with open(path, 'rb') as f:
                body = f.read()
            self.send_response(200)
            self.send_header('Content-Type',   ctype)
            self.send_header('Content-Length', len(body))
            self.end_headers()
            self.wfile.write(body)
        except FileNotFoundError:
            self.send_response(404); self.end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_POST(self):
        path = urlparse(self.path).path

        # ── LLM Risk/Reward analysis for one stock ───────────────────
        if path.startswith('/api/analyze/'):
            ticker = path.replace('/api/analyze/', '').upper().strip()
            with state_lock:
                stock = next((s for s in state['stocks'] if s['ticker'] == ticker), None)
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
                    'ist_time':       get_ist().strftime('%H:%M:%S'),
                    'fetch_progress': 100 if state['status'] not in ('scanning','starting') else 50,
                    'fetch_message':  state['status'],
                    'total_scanned':  state['count'],
                    'in_range':       state['count'],
                }
            self.send_json(data)
            return

        # ── Stocks ──────────────────────────────────────────────────
        if path == '/api/stocks':
            with state_lock:
                data = {
                    'status':       state['status'],
                    'market_mode':  state['market_mode'],
                    'last_updated': state['last_updated'],
                    'stocks':       list(state['stocks']),
                    'indices':      dict(state['indices']),
                }
            self.send_json(data)
            return

        # ── Prices (lightweight) ────────────────────────────────────
        if path == '/api/prices':
            with state_lock:
                prices = [
                    {'ticker': s['ticker'], 'price': s['price'], 'change': s['change']}
                    for s in state['stocks']
                ]
                status       = state['status']
                last_updated = state['last_updated']
            self.send_json({'status': status, 'last_updated': last_updated, 'prices': prices})
            return

        # ── Single stock ────────────────────────────────────────────
        if path.startswith('/api/stock/'):
            ticker = path.replace('/api/stock/', '').upper().strip()
            with state_lock:
                stock = next((s for s in state['stocks'] if s['ticker'] == ticker), None)
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
            ctrl = {
                'nse':     read_status_file('nse'),
                'kite':    read_status_file('kite'),
                'yf':      read_status_file('yf'),
                'compute': read_status_file('compute'),
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
                    'nse_universe':  read_status_file('nse_universe'),
                    'nse':           read_status_file('nse'),
                    'kite':          read_status_file('kite'),
                    'yf':            read_status_file('yf'),
                    'compute':       read_status_file('compute'),
                })
            return

        # ── Indices — NIFTY 50 + SENSEX via Kite ltp() ─────────────
        if path == '/api/indices':
            try:
                kite   = load_kite()
                keys   = ['NSE:NIFTY 50', 'BSE:SENSEX']
                data   = kite.ltp(keys)
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

    threading.Thread(target=scheduler, daemon=True).start()

    try:
        server = ThreadingHTTPServer(('0.0.0.0', PORT), Handler)
    except OSError:
        print(f'\n  Port {PORT} is already in use!')
        print(f'  Close existing instance first.')
        input('\n  Press Enter to exit...')
        sys.exit(1)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n  Server stopped.')


if __name__ == '__main__':
    main()
