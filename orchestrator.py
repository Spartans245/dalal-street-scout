"""
orchestrator.py — Pipeline Controller
======================================
Runs workers in parallel where possible, checks exit codes and status JSONs.
Called by server.py when a manual scan is triggered, or directly from CLI.

Pipeline:
  1. nse_worker.py --universe             (skip if < 24h old)
  2. nse_worker.py --fundamentals  ─┐
     kite_worker.py --scan         ─┤  (parallel — compute starts as soon as these two finish)
     yf_worker.py  --fundamentals   │  (parallel, background — compute doesn't wait)
  3. compute.py                      │  (starts immediately after NSE+Kite done)
     yf_worker.py (still running)  ─┘  (yf non-blocking: failures tolerated)

Exit codes:
  0 = success (compute done, stocks.json ready)
  1 = fatal (pipeline aborted)
  2 = auth required (Kite token stale)

Usage:
  python orchestrator.py              # full pipeline
  python orchestrator.py --test       # test mode: 5 tickers per worker
  python orchestrator.py --from kite  # resume from kite_worker (skip NSE)
  python orchestrator.py --from compute  # just re-run compute (all raw available)
"""

import sys, os, json, time, datetime, subprocess, argparse

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
STATUS_DIR    = os.path.join(BASE_DIR, 'data', 'status')
RAW_DIR       = os.path.join(BASE_DIR, 'data', 'raw')
COMPUTED_FILE = os.path.join(BASE_DIR, 'data', 'computed', 'stocks.json')
YF_RAW_FILE   = os.path.join(BASE_DIR, 'data', 'raw', 'yf.json')

PYTHON = sys.executable

# ────────────────────────────────────────────────────────────────────
# Status JSON helpers
# ────────────────────────────────────────────────────────────────────
def read_status(name):
    """Read data/status/{name}.json. Returns dict or {}."""
    path = os.path.join(STATUS_DIR, f'{name}.json')
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def print_status(name, st):
    status   = st.get('status', '?')
    count    = st.get('count', 0)
    errors   = st.get('errors', 0)
    duration = st.get('duration', 0)
    message  = st.get('message', '')
    print(f'  [{name.upper()}] {status} | {count} items | {errors} errors | {duration:.0f}s | {message}')


# ────────────────────────────────────────────────────────────────────
# Freshness check — skip if status file was written within threshold
# ────────────────────────────────────────────────────────────────────
def is_fresh(name, max_age_hours=24):
    """Return True if the worker's status is 'done' and < max_age_hours old."""
    st = read_status(name)
    if st.get('status') != 'done':
        return False
    saved_at = st.get('saved_at', '')
    if not saved_at:
        return False
    try:
        ts  = datetime.datetime.fromisoformat(saved_at)
        age = (datetime.datetime.now() - ts.replace(tzinfo=None)).total_seconds() / 3600
        return age < max_age_hours
    except Exception:
        return False


# ────────────────────────────────────────────────────────────────────
# YF patch — inject D/E + ROE into stocks.json without full recompute
# ────────────────────────────────────────────────────────────────────
def patch_yf_into_computed():
    """
    After YF finishes, patch debtEq + roe directly into stocks.json.
    The server's file watcher detects the mtime change and reloads the UI.
    No compute needed — these fields are display-only.
    """
    try:
        if not os.path.exists(YF_RAW_FILE):
            print('[ORCH] patch_yf: yf.json not found — skipping patch')
            return
        if not os.path.exists(COMPUTED_FILE):
            print('[ORCH] patch_yf: stocks.json not found — skipping patch')
            return

        with open(YF_RAW_FILE, encoding='utf-8') as f:
            yf = json.load(f)   # {symbol: {debtEq, roe, ...}}
        with open(COMPUTED_FILE, encoding='utf-8') as f:
            stocks = json.load(f)

        def de_flag(de):
            if de is None:            return None
            if de < 0.05:             return 'DEBT FREE'
            if de < 0.5:              return 'LOW DEBT'
            if de < 1.0:              return 'MODERATE'
            if de < 2.0:              return 'HIGH DEBT'
            return 'LEVERAGED'

        def roe_flag(roe):
            if roe is None:           return None
            if roe >= 20:             return 'EXCEL'
            if roe >= 12:             return 'GOOD'
            if roe >= 0:              return 'WEAK'
            return 'NEGATIVE'

        patched = 0
        for s in stocks:
            sym = s.get('ticker', s.get('symbol', ''))
            if sym in yf:
                entry = yf[sym]
                de  = entry.get('debtEq')
                roe = entry.get('roe')
                if de  is not None: s['debtEq']  = de
                if roe is not None: s['roe']      = roe
                s['deFlag']  = de_flag(de)
                s['roeFlag'] = roe_flag(roe)
                patched += 1

        with open(COMPUTED_FILE, 'w', encoding='utf-8') as f:
            json.dump(stocks, f, separators=(',', ':'))

        print(f'[ORCH] patch_yf: patched {patched}/{len(stocks)} stocks with D/E + ROE → UI will reload')
    except Exception as e:
        print(f'[ORCH] patch_yf: error — {e}')


# ────────────────────────────────────────────────────────────────────
# Run a worker subprocess
# ────────────────────────────────────────────────────────────────────
def run_worker(script, args_list, label):
    """Run a Python worker script. Returns exit code."""
    cmd = [PYTHON, '-W', 'ignore', os.path.join(BASE_DIR, script)] + args_list
    print(f'\n[ORCH] Running: {" ".join(cmd)}')
    t0 = time.time()
    try:
        result = subprocess.run(cmd, cwd=BASE_DIR)
        elapsed = round(time.time() - t0, 1)
        code = result.returncode
        print(f'[ORCH] {label} exited {code} in {elapsed}s')
        return code
    except Exception as e:
        print(f'[ORCH] Failed to launch {script}: {e}')
        return 1


def run_parallel_workers(workers, label):
    """
    Launch multiple workers simultaneously. Wait for all to finish.
    workers: list of (name, script, args_list)
    Returns: dict of {name: exit_code}
    """
    print(f'\n[ORCH] Launching parallel: {", ".join(n for n,_,_ in workers)}')
    t0 = time.time()
    procs = {}
    for name, script, args_list in workers:
        cmd = [PYTHON, '-W', 'ignore', os.path.join(BASE_DIR, script)] + args_list
        try:
            procs[name] = subprocess.Popen(cmd, cwd=BASE_DIR,
                creationflags=subprocess.BELOW_NORMAL_PRIORITY_CLASS)
        except Exception as e:
            print(f'[ORCH] Failed to launch {script}: {e}')
            procs[name] = None

    results = {}
    for name, proc in procs.items():
        if proc is None:
            results[name] = 1
        else:
            proc.wait()
            results[name] = proc.returncode

    elapsed = round(time.time() - t0, 1)
    for name, code in results.items():
        print(f'[ORCH] {name} exited {code}')
    print(f'[ORCH] Parallel block done in {elapsed}s')
    return results


# ────────────────────────────────────────────────────────────────────
# Pipeline steps
# ────────────────────────────────────────────────────────────────────
UNIVERSE_MAX_AGE = 24    # hours — refresh NSE universe once per day
FUND_MAX_AGE     = 23    # hours — refresh fundamentals before each scan

STEPS = {
    'nse_universe':     ('nse_worker.py',  ['--universe']),
    'nse_fundamentals': ('nse_worker.py',  ['--fundamentals']),
    'kite_scan':        ('kite_worker.py', ['--scan']),
    'yf_fundamentals':  ('yf_worker.py',   ['--fundamentals']),
    'compute':          ('compute.py',     []),
}

STEPS_TEST = {
    'nse_universe':     ('nse_worker.py',  ['--universe']),
    'nse_fundamentals': ('nse_worker.py',  ['--test']),
    'kite_scan':        ('kite_worker.py', ['--test']),
    'yf_fundamentals':  ('yf_worker.py',   ['--test']),
    'compute':          ('compute.py',     ['--test']),
}


def run_pipeline(test=False, from_step=None):
    """
    Run the full pipeline. Returns exit code: 0=success, 1=fatal, 2=auth.
    from_step: 'nse' | 'kite' | 'yf' | 'compute' — skip earlier steps
    """
    steps = STEPS_TEST if test else STEPS
    t0    = time.time()

    print(f'\n{"="*60}')
    print(f'[ORCH] Pipeline start {"(TEST MODE)" if test else ""}')
    print(f'{"="*60}')

    skip_until = from_step  # None = run all

    # ── Step 1: NSE Universe ──────────────────────────────────────
    # Always refresh on a full scan (EQUITY_L.csv + NSE SME Emerge) — fast download
    if skip_until not in ('kite', 'yf', 'compute'):
        code = run_worker(*steps['nse_universe'], label='NSE Universe')
        if code != 0:
            print(f'[ORCH] FATAL: NSE universe failed (exit {code})')
            return 1
        st = read_status('nse')
        print_status('nse', st)

    # ── Step 2: NSE Fundamentals + Kite OHLCV + YF (parallel) ────
    # NSE + Kite are blocking (compute waits for them).
    # YF runs in background — compute starts without waiting.
    yf_proc = None
    if skip_until not in ('yf', 'compute'):
        # Launch YF in background immediately
        yf_script, yf_args = steps['yf_fundamentals']
        yf_cmd = [PYTHON, '-W', 'ignore', os.path.join(BASE_DIR, yf_script)] + yf_args
        print(f'\n[ORCH] Launching background: YF D/E+ROE')
        try:
            yf_proc = subprocess.Popen(yf_cmd, cwd=BASE_DIR,
                creationflags=subprocess.BELOW_NORMAL_PRIORITY_CLASS)
        except Exception as e:
            print(f'[ORCH] WARN: Could not launch yf_worker: {e}')

        # Launch NSE fundamentals + Kite in parallel, wait for both
        blocking = []
        if skip_until not in ('kite', 'yf'):
            nse_script, nse_args = steps['nse_fundamentals']
            blocking.append(('NSE Fundamentals', nse_script, nse_args))
        kite_script, kite_args = steps['kite_scan']
        blocking.append(('Kite OHLCV Scan', kite_script, kite_args))

        results = run_parallel_workers(blocking, 'NSE+Kite')

        nse_code  = results.get('NSE Fundamentals', 0)
        kite_code = results.get('Kite OHLCV Scan', 0)

        if 'NSE Fundamentals' in results:
            if nse_code != 0:
                print(f'[ORCH] FATAL: NSE fundamentals failed (exit {nse_code})')
                return 1
            print_status('nse', read_status('nse'))

        if kite_code == 2:
            print(f'[ORCH] AUTH REQUIRED: Kite token stale — run kite_auth.py')
            return 2
        if kite_code != 0:
            print(f'[ORCH] FATAL: Kite scan failed (exit {kite_code})')
            return 1
        print_status('kite', read_status('kite'))

    # ── Step 3: Compute (starts immediately — YF may still be running) ─
    code = run_worker(*steps['compute'], label='Compute')
    if code != 0:
        print(f'[ORCH] FATAL: Compute failed (exit {code})')
        return 1
    st = read_status('compute')
    print_status('compute', st)

    # ── Wait for YF, then patch D/E+ROE into stocks.json ─────────
    if yf_proc is not None:
        if yf_proc.poll() is None:
            print(f'[ORCH] Waiting for YF background process to finish...')
        yf_proc.wait()
        yf_code = yf_proc.returncode
        if yf_code != 0:
            print(f'[ORCH] WARN: YF fundamentals failed (exit {yf_code}) — D/E and ROE unavailable')
        else:
            print_status('yf', read_status('yf'))
            patch_yf_into_computed()   # triggers server file-watcher reload → UI refreshes

    # ── Summary ───────────────────────────────────────────────────
    elapsed = round(time.time() - t0, 1)
    count   = st.get('count', 0)
    errors  = st.get('errors', 0)
    print(f'\n{"="*60}')
    print(f'[ORCH] Pipeline complete: {count} stocks in {elapsed}s ({errors} compute errors)')
    print(f'{"="*60}\n')
    return 0


# ────────────────────────────────────────────────────────────────────
# MAIN
# ────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--test', action='store_true', help='Test mode: 5 tickers per worker')
    parser.add_argument('--from', dest='from_step', default=None,
                        choices=['kite', 'yf', 'compute'],
                        help='Resume pipeline from this step (skip earlier workers)')
    args = parser.parse_args()

    code = run_pipeline(test=args.test, from_step=args.from_step)
    sys.exit(code)


if __name__ == '__main__':
    main()
