"""
compute.py — Data Merge + Formula Engine
=========================================
Reads raw staged data from all three workers, applies all formulas,
and writes the final computed stock list.

Input:
  data/raw/kite.json   — OHLCV rows per stock (from kite_worker.py --scan)
  data/raw/nse.json    — PE, MCap, sector, name (from nse_worker.py --fundamentals)
  data/raw/yf.json     — D/E ratio, ROE       (from yf_worker.py --fundamentals)

Output:
  data/computed/stocks.json  — full computed stock list (server.py reads this)
  data/status/compute.json   — exit status, count, duration, errors

Exit codes:
  0 = success
  1 = fatal error

Usage:
  python compute.py             # full compute
  python compute.py --test      # test mode: first 5 stocks from kite.json
"""

import sys, os, json, time, datetime, math, argparse

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

import warnings
warnings.filterwarnings('ignore')

BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
RAW_DIR       = os.path.join(BASE_DIR, 'data', 'raw')
COMPUTED_DIR  = os.path.join(BASE_DIR, 'data', 'computed')
STATUS_DIR    = os.path.join(BASE_DIR, 'data', 'status')
KITE_FILE     = os.path.join(RAW_DIR,     'kite.json')
NSE_FILE      = os.path.join(RAW_DIR,     'nse.json')
YF_FILE       = os.path.join(RAW_DIR,     'yf.json')
COMPUTED_FILE = os.path.join(COMPUTED_DIR,'stocks.json')
STATUS_FILE   = os.path.join(STATUS_DIR,  'compute.json')
SIGNALS_FILE  = os.path.join(BASE_DIR,    'data', 'signals_log.json')

os.makedirs(COMPUTED_DIR, exist_ok=True)
os.makedirs(STATUS_DIR,   exist_ok=True)

sys.path.insert(0, BASE_DIR)
from shared.technicals import (
    calc_technicals, classify_stage, score, calc_target,
    enrich_sector_pe, calc_swing_sr, _safe_json,
)

try:
    import pandas as pd
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'pandas', '-q'])
    import pandas as pd


# ════════════════════════════════════════════════════════════════════
# STATUS WRITER
# ════════════════════════════════════════════════════════════════════
def write_status(status, count=0, errors=0, duration=0, message=''):
    data = {
        'status':   status,
        'count':    count,
        'errors':   errors,
        'duration': round(duration, 1),
        'message':  message,
        'saved_at': datetime.datetime.now().isoformat(),
    }
    with open(STATUS_FILE, 'w') as f:
        json.dump(data, f, indent=2)


# ════════════════════════════════════════════════════════════════════
# RAW DATA LOADERS
# ════════════════════════════════════════════════════════════════════
def load_kite_raw():
    """Load data/raw/kite.json. Returns ohlcv dict {symbol: {token, board, rows}}."""
    if not os.path.exists(KITE_FILE):
        return None
    try:
        with open(KITE_FILE) as f:
            data = json.load(f)
        ohlcv = data.get('ohlcv', {})
        print(f'[COMPUTE] Kite raw: {len(ohlcv)} symbols loaded')
        return ohlcv
    except Exception as e:
        print(f'[COMPUTE] Failed to load kite.json: {e}')
        return None


def load_nse_raw():
    """Load data/raw/nse.json. Returns fundamentals dict {symbol: {pe, mcap, sector, name, ...}}."""
    if not os.path.exists(NSE_FILE):
        print('[COMPUTE] WARN: nse.json not found — PE/MCap/sector will be missing')
        return {}
    try:
        with open(NSE_FILE) as f:
            data = json.load(f)
        fund = data.get('fundamentals', {})
        print(f'[COMPUTE] NSE fundamentals: {len(fund)} stocks')
        return fund
    except Exception as e:
        print(f'[COMPUTE] WARN: Failed to load nse.json: {e}')
        return {}


def load_yf_raw():
    """Load data/raw/yf.json. Returns {symbol: {debtEq, roe}} or {}."""
    if not os.path.exists(YF_FILE):
        print('[COMPUTE] WARN: yf.json not found — D/E and ROE will be null')
        return {}
    try:
        with open(YF_FILE) as f:
            data = json.load(f)
        fund = data.get('fundamentals', {})
        print(f'[COMPUTE] YF fundamentals: {len(fund)} stocks with D/E or ROE')
        return fund
    except Exception as e:
        print(f'[COMPUTE] WARN: Failed to load yf.json: {e}')
        return {}


# ════════════════════════════════════════════════════════════════════
# BUILD DATAFRAME FROM RAW OHLCV ROWS
# rows = [[date_str, open, high, low, close, volume], ...]
# ════════════════════════════════════════════════════════════════════
def rows_to_df(rows):
    """Convert OHLCV row list to pandas DataFrame. Returns None if < 30 rows."""
    try:
        df = pd.DataFrame(rows, columns=['Date','Open','High','Low','Close','Volume'])
        df['Date'] = pd.to_datetime(df['Date'])
        df = df.set_index('Date').sort_index()
        df = df[df['Close'] > 0]
        return df if len(df) >= 30 else None
    except Exception as e:
        return None


# ════════════════════════════════════════════════════════════════════
# COMPUTE ONE STOCK
# ════════════════════════════════════════════════════════════════════
def compute_one(symbol, kite_entry, nse_fund, yf_fund):
    """Compute all fields for one stock. Returns stock dict or None."""
    try:
        rows = kite_entry.get('rows', [])
        board = kite_entry.get('board', 'main')

        hist = rows_to_df(rows)
        if hist is None:
            return None

        # ── Price from last OHLCV row ────────────────────────────────
        last_row   = hist.iloc[-1]
        prev_row   = hist.iloc[-2] if len(hist) > 1 else last_row
        price      = round(float(last_row['Close']), 2)
        prev_close = round(float(prev_row['Close']), 2)
        change     = round((price - prev_close) / prev_close * 100, 2) if prev_close else 0.0

        if not price or math.isnan(price) or price <= 0:
            return None

        # ── 38W High/Low from last 193 trading days (Kite depth) ────
        highs = hist['High'].values
        lows  = hist['Low'].values
        wk38h = round(float(max(highs[-193:])) if len(highs) >= 193 else float(max(highs)), 2)
        wk38l = round(float(min(lows[-193:]))  if len(lows)  >= 193 else float(min(lows)),  2)

        pct38h = round((price - wk38h) / wk38h * 100, 1) if wk38h else 0.0
        pct38l = round((price - wk38l) / wk38l * 100, 1) if wk38l else 0.0

        # ── ATH(9M) — all-time high within Kite history window ───────
        ath = 0.0
        try:
            ath_raw = float(hist['High'].max())
            if not math.isnan(ath_raw):
                ath = round(ath_raw, 2)
        except Exception:
            pass

        # ── Avg 20d volume & daily vol (Cr) ─────────────────────────
        avg20_base = float(hist['Volume'].tail(20).mean())
        dvol       = round(avg20_base * price / 1e7, 1)  # Crores

        # ── NSE fundamentals ─────────────────────────────────────────
        nse  = nse_fund.get(symbol, {})
        pe   = float(nse.get('pe') or 0)
        mcap = int(nse.get('mcap') or 0)

        # NSE sector label; fallback chain — guard against encoding corruption
        sector = nse.get('sector') or 'Others'
        if not isinstance(sector, str) or len(sector) > 100:
            sector = 'Others'
        name   = nse.get('name') or symbol
        if not isinstance(name, str) or len(name) > 200:
            name = symbol

        # NSE true 52W high/low (separate from Kite 38W)
        wk52High_nse = float(nse.get('wk52High') or 0)
        wk52Low_nse  = float(nse.get('wk52Low')  or 0)

        # ── YF supplemental ─────────────────────────────────────────
        yf   = yf_fund.get(symbol, {})
        de   = yf.get('debtEq')   # float or None
        roe  = yf.get('roe')      # float % or None

        roe_warn = 'na' if roe is None else ('low' if roe < 12 else 'ok')

        # ── D/E and ROE flags (display badges, no raw numbers) ───────
        if de is None:
            de_flag = None
        elif de < 0.05:
            de_flag = 'DEBT FREE'
        elif de < 0.5:
            de_flag = 'LOW DEBT'
        elif de < 1.0:
            de_flag = 'MODERATE'
        elif de < 2.0:
            de_flag = 'HIGH DEBT'
        else:
            de_flag = 'LEVERAGED'

        if roe is None:
            roe_flag = None
        elif roe >= 20:
            roe_flag = 'EXCEL'
        elif roe >= 12:
            roe_flag = 'GOOD'
        elif roe >= 0:
            roe_flag = 'WEAK'
        else:
            roe_flag = 'NEGATIVE'

        # ── Technicals ───────────────────────────────────────────────
        tech = calc_technicals(hist)

        vpb_rh    = tech['vpb_range_height'] if tech else 0.0
        mm_target = round(price + vpb_rh, 2) if vpb_rh > 0 else None

        # Compute stage first so calc_target can filter MM by stage
        stage = classify_stage(tech)

        # calc_target uses NSE 52W high as the medium-term high reference
        target_price, target_type, upside_pct, upside_rs, mm_conditional = calc_target(
            price, mm_target, wk52High_nse, ath, stage
        )

        # ── Score (sector PE enrichment done post-loop) ──────────────
        sc, f, c, t2, ct2, l = score(pe, de, roe, dvol, tech)

        # ── Swing S/R levels ─────────────────────────────────────────
        sr = calc_swing_sr(highs, lows, price, window=5, cluster_pct=1.5, n=3)

        # ── Backward stage snapshots for EVALS (only for actionable stages) ──
        prev_stages = {}
        if stage in _ACTIONABLE_STAGES:
            for lookback in [5, 10, 20]:
                if len(hist) > lookback + 30:
                    try:
                        t_hist = calc_technicals(hist.iloc[:-lookback])
                        if t_hist:
                            prev_stages[f'd_minus_{lookback}'] = classify_stage(t_hist)
                    except Exception:
                        pass

        # ── Chart data (last 60 closes) ──────────────────────────────
        chart_prices, chart_dates = [], []
        try:
            h60 = hist.tail(60)
            chart_prices = [
                round(float(x), 2)
                for x in h60['Close'].ffill().tolist()
                if not math.isnan(float(x))
            ]
            chart_dates = [str(d.date()) for d in h60.index.tolist()]
        except Exception:
            pass

        return {
            'ticker':          symbol,
            'name':            name,
            'sector':          sector,
            'board':           board,
            'price':           price,
            'prevClose':       prev_close,
            'change':          change,
            'pe':              pe,
            'mcap':            mcap,
            'promoterHolding': 0,
            'pledging':        0,
            'debtEq':          de,
            'roe':             roe,
            'roeWarn':         roe_warn,
            'deFlag':          de_flag,
            'roeFlag':         roe_flag,
            'wk38High':        wk38h,
            'wk38Low':         wk38l,
            'wk52High':        wk52High_nse,
            'wk52Low':         wk52Low_nse,
            'near52High':      bool(price and wk52High_nse and price >= wk52High_nse * 0.92),
            'pctFrom38High':   pct38h,
            'pctFrom38Low':    pct38l,
            'rsi':             tech['rsi']                  if tech else 50.0,
            'adx':             tech['adx']                  if tech else 15.0,
            'macd':            tech['macd']                 if tech else False,
            'emaSignal':       tech['ema_signal']           if tech else 'none',
            'emaCross':        tech['ema_cross']            if tech else False,
            'emaCrossDays':    tech['ema_cross_days_ago']   if tech else None,
            'emaTrend':        tech['ema_trend']            if tech else False,
            'volConfirm':      tech['vol_confirmed_cross']  if tech else False,
            'crossScore':      tech['cross_score']          if tech else 0,
            'emaPreCross':     tech['ema_pre_cross']        if tech else False,
            'emaPostCross':    tech['ema_post_cross']       if tech else False,
            'emaPullback':     tech['ema_pullback']         if tech else False,
            'golden':          tech['golden']               if tech else False,
            'vpbScore':        tech['vpb_score']            if tech else 0,
            'vpbDetail':       tech['vpb_detail']           if tech else 'none',
            'vpbRangeHeight':  tech['vpb_range_height']     if tech else 0.0,
            'avg20Vol':        tech['avg20_vol']             if tech else 0.0,
            'priceCoiling':    tech['price_coiling']         if tech else False,
            'volShrinking':    tech['vol_shrinking']         if tech else False,
            'volRatio':        tech['vol_ratio']             if tech else 0.0,
            'closePos':        tech['close_pos']             if tech else 0.0,
            'ema14':           tech['ema14']                 if tech else 0.0,
            'ema50':           tech['ema50']                 if tech else 0.0,
            'emaGapPct':       tech['ema_gap_pct']           if tech else 0.0,
            'ema14Rising':     tech['ema14_rising']          if tech else False,
            'ema14RisingFast': tech['ema14_rising_fast']     if tech else False,
            'near38High':      tech['near_38high']          if tech else False,
            'stage':           stage,
            'catalysts':       [],
            'dailyVol':        dvol,
            'score':           sc,
            'fScore':          f,
            'cScore':          c,
            'tScore':          t2,
            'ctScore':         ct2,
            'lScore':          l,
            'chartPrices':     chart_prices,
            'chartDates':      chart_dates,
            'ath':             ath,
            'mmTarget':        mm_target,
            'targetPrice':     target_price,
            'targetType':      target_type,
            'upsidePct':       upside_pct,
            'upsideRs':        upside_rs,
            'mmConditional':   mm_conditional,
            'data_source':     'kite',
            'srResistance':    sr['resistance'],
            'srSupport':       sr['support'],
            'prevStages':      prev_stages,   # EVALS: {d_minus_5/10/20: stage}
            # Sector PE fields — filled by enrich_sector_pe() after loop
            'sectorMedianPe':  None,
            'peVsSector':      None,
        }

    except Exception as e:
        print(f'[COMPUTE] {symbol}: {e}')
        return None


# ════════════════════════════════════════════════════════════════════
# SIGNAL LOGGER — records new actionable signals to data/signals_log.json
# Called once per full compute run (skipped in --test mode).
# ════════════════════════════════════════════════════════════════════
_ACTIONABLE_STAGES = {'post_cross', 'pre_cross', 'breakout', 'pullback'}


def _calc_sig_score(s):
    """Python replica of JS signal score: sigScore tier logic."""
    stage = s.get('stage', 'none')
    if stage in ('trending', 'none', 'coiling'):
        return 0
    vpb   = s.get('vpbScore',   0) or 0
    cross = s.get('crossScore', 0) or 0
    pre   = bool(s.get('emaPreCross'))
    if pre and vpb > 0:
        return 18 if vpb >= 10 else (14 if vpb >= 7 else 12)
    if cross > 0:
        return cross + (3 if stage == 'pullback' else 0)
    if vpb > 0:
        return vpb
    return 0


def _compute_outcome(sig):
    """Compute WIN/LOSS/OPEN outcome for a signal. Updates sig in-place."""
    prices_dict = sig.get('prices', {})
    if not prices_dict:
        return
    p0 = sig.get('price_d0', 0)
    if not p0:
        return
    sorted_dates = sorted(prices_dict.keys())
    if len(sorted_dates) < 30:
        return   # need at least 30 trading days to evaluate
    for day_idx, date_str in enumerate(sorted_dates):
        price = prices_dict[date_str]
        if not price:
            continue
        # Stop loss: price fell ≥ 9% below entry
        if price <= p0 * 0.91:
            sig['outcome']       = 'LOSS'
            sig['outcome_day']   = day_idx
            sig['outcome_price'] = price
            sig['outcome_ret']   = round((price - p0) / p0 * 100, 2)
            return
        # WIN: price ≥ 15% above entry, within D30–D60 window
        if 30 <= day_idx <= 60 and price >= p0 * 1.15:
            sig['outcome']       = 'WIN'
            sig['outcome_day']   = day_idx
            sig['outcome_price'] = price
            sig['outcome_ret']   = round((price - p0) / p0 * 100, 2)
            return
    # Signal expired without reaching either threshold
    if len(sorted_dates) > 60:
        sig['outcome']       = 'LOSS'
        sig['outcome_day']   = 60
        sig['outcome_price'] = prices_dict.get(sorted_dates[60], p0)
        sig['outcome_ret']   = round((sig['outcome_price'] - p0) / p0 * 100, 2)


def log_signals(results):
    """EOD 3:30 PM only: add top-10 new signals per stage (sorted by score),
    update stage_history for existing open signals, recompute outcomes."""
    today_str  = datetime.datetime.now().date().isoformat()
    today_date = datetime.date.fromisoformat(today_str)

    # Load existing log
    existing = []
    if os.path.exists(SIGNALS_FILE):
        try:
            with open(SIGNALS_FILE, encoding='utf-8') as f:
                existing = json.load(f).get('signals', [])
        except Exception:
            pass

    # Build results lookup for stage tracking
    results_map = {s.get('ticker', ''): s for s in results if s.get('ticker')}

    # Most-recent entry per ticker (for new signal dedup)
    latest = {}
    for e in existing:
        t = e.get('ticker', '')
        if t and (t not in latest or e.get('day0', '') > latest[t].get('day0', '')):
            latest[t] = e

    # ── Update existing open signals ──────────────────────────────
    updated_count = 0
    for sig in existing:
        if sig.get('outcome') in ('WIN', 'LOSS'):
            continue   # already closed — skip
        ticker = sig.get('ticker', '')

        # Append today's stage to stage_history (only if changed)
        current_stock = results_map.get(ticker)
        if current_stock:
            current_stage = current_stock.get('stage', 'none')
            hist = sig.setdefault('stage_history', [])
            if not hist or hist[-1].get('stage') != current_stage:
                hist.append({'date': today_str, 'stage': current_stage})
                updated_count += 1

        # Recompute outcome (only when we have ≥30 price entries)
        _compute_outcome(sig)

    # ── Add new signals — top 10 per stage by score ──────────────
    new_count = 0
    from collections import defaultdict
    stage_candidates = defaultdict(list)
    for s in results:
        stage = s.get('stage', 'none')
        if stage not in _ACTIONABLE_STAGES:
            continue
        ticker = s.get('ticker', '')
        if not ticker:
            continue
        # Skip if same stage already logged within last 5 days
        prev = latest.get(ticker)
        if prev and prev.get('stage') == stage:
            try:
                gap = (today_date - datetime.date.fromisoformat(prev.get('day0', ''))).days
                if gap < 5:
                    continue
            except Exception:
                pass
        sig   = _calc_sig_score(s)
        total = (s.get('fScore', 0) or 0) + (s.get('tScore', 0) or 0) + sig
        stage_candidates[stage].append((total, sig, s))

    # Take top 10 per stage (sorted by score descending)
    retrigger_count = 0
    for stage, candidates in stage_candidates.items():
            top10 = sorted(candidates, key=lambda x: x[0], reverse=True)[:10]
            for total, sig, s in top10:
                ticker = s.get('ticker', '')

                # If an OPEN signal already exists for this ticker, record as re-trigger
                # (extends the tracking window; D0 stays the same)
                prev = latest.get(ticker)
                if prev and prev.get('outcome') == 'OPEN':
                    rdates = prev.setdefault('retrigger_dates', [])
                    if today_str not in rdates:
                        rdates.append(today_str)
                        retrigger_count += 1
                    continue  # don't create a new entry

                entry = {
                    'id':             f'{ticker}_{today_str}',
                    'ticker':         ticker,
                    'name':           s.get('name', ticker),
                    'sector':         s.get('sector', 'Others'),
                    'day0':           today_str,
                    'stage':          stage,
                    'score':          total,
                    'fScore':         s.get('fScore',      0) or 0,
                    'tScore':         s.get('tScore',      0) or 0,
                    'sigScore':       sig,
                    'rsi':            round(s.get('rsi',   0) or 0, 1),
                    'adx':            round(s.get('adx',   0) or 0, 1),
                    'pe':             round(s.get('pe',    0) or 0, 1),
                    'mcap':           s.get('mcap',        0) or 0,
                    'board':          s.get('board',     'main'),
                    'vpbScore':       s.get('vpbScore',    0) or 0,
                    'vpbDetail':      s.get('vpbDetail', 'none'),
                    'crossScore':     s.get('crossScore',  0) or 0,
                    'emaPreCross':    bool(s.get('emaPreCross')),
                    'emaCross':       bool(s.get('emaCross')),
                    'emaPullback':    bool(s.get('emaPullback')),
                    'volConfirm':     bool(s.get('volConfirm')),
                    'priceCoiling':   bool(s.get('priceCoiling')),
                    'volShrinking':   bool(s.get('volShrinking')),
                    'deFlag':         s.get('deFlag'),
                    'roeFlag':        s.get('roeFlag'),
                    'price_d0':       s.get('price', 0),
                    'nifty_d0':       0,
                    'source':         'auto',
                    'prices':         {today_str: s.get('price', 0)},
                    'nifty_prices':   {},
                    'prev_stages':    s.get('prevStages', {}),
                    'stage_history':  [{'date': today_str, 'stage': stage}],
                    'retrigger_dates':[],
                    'outcome':        'OPEN',
                    'outcome_day':    None,
                    'outcome_price':  None,
                    'outcome_ret':    None,
                }
                existing.append(entry)
                latest[ticker] = entry
                new_count += 1

    stage_summary = {st: len(cs) for st, cs in stage_candidates.items()}
    print(f'[COMPUTE] EVALS: candidates per stage {stage_summary} → {new_count} new, {retrigger_count} re-triggers ({len(existing)} total)')

    os.makedirs(os.path.dirname(SIGNALS_FILE), exist_ok=True)
    try:
        with open(SIGNALS_FILE, 'w', encoding='utf-8') as f:
            json.dump({'signals': existing}, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f'[COMPUTE] Signal log write error (non-fatal): {e}')


# ════════════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--test', action='store_true', help='Test: first 5 stocks from kite.json')
    parser.add_argument('--eod',  action='store_true', help='EOD mode: log top-10 EVALS signals per stage')
    args = parser.parse_args()

    t0 = time.time()
    write_status('running', message='Loading raw data...')

    # Load raw sources
    ohlcv = load_kite_raw()
    if ohlcv is None:
        msg = 'kite.json not found — run kite_worker.py --scan first'
        print(f'[COMPUTE] FATAL: {msg}')
        write_status('error', message=msg)
        sys.exit(1)

    nse_fund = load_nse_raw()
    yf_fund  = load_yf_raw()

    # Symbol list
    all_symbols = list(ohlcv.keys())
    if args.test:
        symbols = all_symbols[:5]
        print(f'[COMPUTE] Test mode: {symbols}')
    else:
        symbols = all_symbols
        print(f'[COMPUTE] Computing {len(symbols)} stocks...')

    # Compute loop
    results = []
    errors  = 0
    total   = len(symbols)

    write_status('running', message=f'Computing {total} stocks...')

    for i, sym in enumerate(symbols, 1):
        stock = compute_one(sym, ohlcv[sym], nse_fund, yf_fund)
        if stock:
            results.append(stock)
        else:
            errors += 1

        if i % 200 == 0 or i == total:
            pct = int(i / total * 100)
            print(f'[COMPUTE] {i}/{total} ({pct}%) — {len(results)} ok, {errors} errors')
            write_status('running', count=len(results), errors=errors,
                         duration=time.time() - t0,
                         message=f'Computing {i}/{total}...')

    if not results:
        msg = 'Zero stocks computed — check kite.json and shared/technicals.py'
        print(f'[COMPUTE] FATAL: {msg}')
        write_status('error', errors=errors, duration=time.time() - t0, message=msg)
        sys.exit(1)

    # Sector PE enrichment (modifies results in-place, re-scores fScore+score)
    print(f'[COMPUTE] Enriching sector PE...')
    enrich_sector_pe(results)

    # EVALS signal logging moved to eval_worker.py — do not log here

    # Stage counts for diagnostics
    from collections import Counter
    stage_counts = Counter(s['stage'] for s in results)
    print(f'[COMPUTE] Stage distribution: {dict(stage_counts)}')

    # Write computed output
    duration = time.time() - t0
    output = {
        'saved_at':     datetime.datetime.now().isoformat(),
        'count':        len(results),
        'errors':       errors,
        'stage_counts': dict(stage_counts),
        'stocks':       results,
    }

    try:
        raw_str = _safe_json(output)
        tmp_file = COMPUTED_FILE + '.tmp'
        with open(tmp_file, 'w', encoding='utf-8') as f:
            f.write(raw_str)
        os.replace(tmp_file, COMPUTED_FILE)  # atomic swap — no half-written reads
        print(f'[COMPUTE] Saved {len(results)} stocks → {COMPUTED_FILE}')
    except Exception as e:
        msg = f'Failed to write {COMPUTED_FILE}: {e}'
        print(f'[COMPUTE] FATAL: {msg}')
        write_status('error', duration=duration, message=msg)
        sys.exit(1)

    write_status(
        'done',
        count=len(results),
        errors=errors,
        duration=duration,
        message=f'Computed: {len(results)} stocks, {errors} errors, {duration:.0f}s',
    )
    print(f'[COMPUTE] Done: {len(results)} stocks in {duration:.0f}s ({errors} errors)')
    sys.exit(0)


if __name__ == '__main__':
    main()
