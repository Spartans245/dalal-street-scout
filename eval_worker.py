"""
eval_worker.py — EVALS Signal Tracker
======================================
Dedicated worker for all EVALS-specific operations.
NEVER modifies scanner output files — reads them only.

Modes:
  python eval_worker.py --eod            # EOD: log new signals + full update
  python eval_worker.py --price-refresh  # Intraday: update prices for open signals

Reads (never writes):
  data/computed/stocks.json  — current stock prices, stages, scores

Writes (EVALS-owned only):
  data/signals_log.json      — signal log with prices, stage history, outcomes

Rules (see INSTRUCTIONS_FOR_EVALS.md):
  - Never import or modify compute.py / kite_worker.py / nse_worker.py
  - Never write to data/raw/* or data/computed/stocks.json
"""

import sys, os, json, datetime, argparse
from collections import defaultdict

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
COMPUTED_FILE = os.path.join(BASE_DIR, 'data', 'computed', 'stocks.json')
SIGNALS_FILE  = os.path.join(BASE_DIR, 'data', 'signals_log.json')

_ALL_STAGES        = {'post_cross', 'pre_cross', 'breakout', 'pullback', 'coiling', 'trending'}
_ACTIONABLE_STAGES = _ALL_STAGES   # track all stages in EVALS


# ── Helpers ──────────────────────────────────────────────────────────────────

_DATE_OVERRIDE = None  # set by --date CLI arg; patched early in __main__ before any calls

def today_str():
    return _DATE_OVERRIDE or datetime.date.today().isoformat()

def today_dt():
    if _DATE_OVERRIDE:
        return datetime.date.fromisoformat(_DATE_OVERRIDE)
    return datetime.date.today()

_HOLIDAYS_CACHE_FILE = os.path.join(BASE_DIR, 'data', 'evals', 'nse_holidays.json')

def _load_nse_holidays():
    """Return set of NSE CM-segment holiday dates (YYYY-MM-DD).
    Cache refreshes once per month. Falls back to empty set on any error."""
    import time as _time
    try:
        if os.path.exists(_HOLIDAYS_CACHE_FILE):
            cached = json.load(open(_HOLIDAYS_CACHE_FILE, encoding='utf-8'))
            # Refresh cache if older than 30 days
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
    """Return True if date_str (YYYY-MM-DD) is a valid NSE trading day."""
    try:
        d = datetime.date.fromisoformat(date_str)
    except Exception:
        return False
    if d.weekday() >= 5:          # Saturday=5, Sunday=6
        return False
    holidays = _load_nse_holidays()
    return date_str not in holidays

def load_stocks():
    """Read stocks.json — returns (stocks_list, nifty_price, trading_date).
    trading_date is the date of the kite scan (from last_updated or price_refreshed_at),
    NOT the system clock — ensures prices are keyed to the correct trading day."""
    try:
        with open(COMPUTED_FILE, encoding='utf-8') as f:
            raw = f.read()
        # Sanitize NaN/Infinity and use raw_decode to handle any trailing bytes
        raw = raw.replace('Infinity', 'null').replace('NaN', 'null').replace('-null', 'null')
        data, _ = json.JSONDecoder().raw_decode(raw)
        stocks = data.get('stocks', [])
        nifty  = data.get('indices', {}).get('NIFTY 50', {}).get('price', 0) or 0
        # Trading date: from price_refreshed_at or last_updated in stocks.json
        # This is the date of the actual market data, not the system clock.
        trading_date = None
        for key in ('price_refreshed_at', 'last_updated', 'saved_at'):
            ts = data.get(key, '')
            if ts and len(ts) >= 10:
                trading_date = ts[:10]
                break
        if not trading_date:
            trading_date = today_str()  # fallback to system date if not found
        # Fallback: read NIFTY close written by kite_worker during EOD
        if not nifty:
            nifty_file = os.path.join(BASE_DIR, 'data', 'evals', 'nifty_close.json')
            if os.path.exists(nifty_file):
                try:
                    with open(nifty_file, encoding='utf-8') as nf:
                        nd = json.load(nf)
                    if nd.get('date') == trading_date:
                        nifty = nd.get('close', 0) or 0
                except Exception:
                    pass
        return stocks, nifty, trading_date
    except Exception as e:
        print(f'[EVAL] Cannot read stocks.json: {e}')
        return [], 0, today_str()

def load_signals():
    """Read signals_log.json — returns list of signal dicts."""
    if not os.path.exists(SIGNALS_FILE):
        return []
    try:
        with open(SIGNALS_FILE, encoding='utf-8') as f:
            return json.load(f).get('signals', [])
    except Exception as e:
        print(f'[EVAL] Cannot read signals_log.json: {e}')
        return []

def save_signals(signals):
    """Write signals list back to signals_log.json (atomic)."""
    os.makedirs(os.path.dirname(SIGNALS_FILE), exist_ok=True)
    tmp = SIGNALS_FILE + '.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump({'signals': signals}, f, ensure_ascii=False, indent=2)
        os.replace(tmp, SIGNALS_FILE)
    except Exception as e:
        print(f'[EVAL] Cannot save signals_log.json: {e}')


# ── Scoring (mirrors JS/compute.py logic, no import needed) ──────────────────

def calc_sig_score(s):
    """Signal-tier score for a stock dict from stocks.json."""
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


# ── Outcome computation ───────────────────────────────────────────────────────

WIN_TARGET       = 1.15   # +15% from D0 entry price
LOSS_TARGET      = 0.91   # −9%  from D0 entry price
MAX_CALENDAR_DAYS = 60    # expire 60 calendar days after D0 (e.g. Mar 24 → May 23)

def compute_outcome(sig):
    """
    Compute WIN/LOSS/OPEN for a signal.
    WIN     = first day price >= entry × 1.15  (any day from D1)
    LOSS    = first day price <= entry × 0.91  (any day; checked before WIN)
    EXPIRED = 60 calendar days elapsed from D0 with no WIN or LOSS
    Updates sig in-place. Already-resolved (WIN/LOSS/EXPIRED) signals are skipped.
    """
    if sig.get('outcome') in ('WIN', 'LOSS', 'EXPIRED'):
        return

    prices_dict = sig.get('prices', {})
    p0  = sig.get('price_d0', 0)
    day0 = sig.get('day0', '')
    if not prices_dict or not p0 or not day0:
        return

    try:
        day0_date   = datetime.date.fromisoformat(day0)
        expiry_date = day0_date + datetime.timedelta(days=MAX_CALENDAR_DAYS)
    except ValueError:
        return

    sorted_dates = sorted(prices_dict.keys())

    for day_idx, date_str in enumerate(sorted_dates):
        price = prices_dict.get(date_str, 0)
        if not price:
            continue
        # Loss checked first — loss takes priority if both thresholds cross same day
        if price <= p0 * LOSS_TARGET:
            sig['outcome']       = 'LOSS'
            sig['outcome_day']   = day_idx
            sig['outcome_date']  = date_str
            sig['outcome_price'] = price
            sig['outcome_ret']   = round((price - p0) / p0 * 100, 2)
            return
        # Win: triggered the moment price first reaches +15%
        if price >= p0 * WIN_TARGET:
            sig['outcome']       = 'WIN'
            sig['outcome_day']   = day_idx
            sig['outcome_date']  = date_str
            sig['outcome_price'] = price
            sig['outcome_ret']   = round((price - p0) / p0 * 100, 2)
            return
        # Expiry: 60 calendar days from D0 have elapsed
        try:
            current_date = datetime.date.fromisoformat(date_str)
        except ValueError:
            continue
        if current_date >= expiry_date:
            sig['outcome']       = 'EXPIRED'
            sig['outcome_day']   = day_idx
            sig['outcome_date']  = date_str
            sig['outcome_price'] = price
            sig['outcome_ret']   = round((price - p0) / p0 * 100, 2)
            return


# ── Tracking window check ─────────────────────────────────────────────────────

def is_within_tracking_window(sig, reference_date=None):
    """
    Returns True if signal should still receive daily price updates.
    Window: MAX_CALENDAR_DAYS + 5 buffer days from D0 (65 calendar days).
    Already-resolved signals stop updating immediately.
    """
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


# ── Core operations ───────────────────────────────────────────────────────────

def build_daily_snapshot(s, nifty_price, sig_score=None):
    """
    Build a full daily snapshot from a stock entry in stocks.json.
    Same fields as D0 — every data point captured every day.
    """
    if sig_score is None:
        sig_score = calc_sig_score(s)
    total = (s.get('fScore', 0) or 0) + (s.get('tScore', 0) or 0) + sig_score
    return {
        # ── Price & volume ────────────────────────────────────────
        'price':          round(float(s.get('price',      0) or 0), 2),
        'nifty':          round(float(nifty_price or 0),            2),
        'volume':         round(float(s.get('dailyVol',   0) or 0), 2),
        'avg_volume':     round(float(s.get('avg20Vol',   0) or 0), 1),
        # ── Scores ────────────────────────────────────────────────
        'score':          total,
        'fScore':         s.get('fScore',    0) or 0,
        'tScore':         s.get('tScore',    0) or 0,
        'sigScore':       sig_score,
        'cScore':         s.get('cScore',    0) or 0,
        'ctScore':        s.get('ctScore',   0) or 0,
        # ── Technicals ────────────────────────────────────────────
        'rsi':            round(float(s.get('rsi',  0) or 0), 1),
        'adx':            round(float(s.get('adx',  0) or 0), 1),
        'macd':           s.get('macd',      0) or 0,
        'upsidePct':      s.get('upsidePct', 0) or 0,
        # ── Phase indicators (VPB raw metrics) ────────────────────
        'volRatio':       round(float(s.get('volRatio',   0) or 0), 3),  # today_vol / 20d_avg
        'closePos':       round(float(s.get('closePos',   0) or 0), 3),  # (close-low)/(high-low)
        'vpbRangeHeight': round(float(s.get('vpbRangeHeight', 0) or 0), 2),
        # ── EMA levels & metrics ──────────────────────────────────
        'ema14':             round(float(s.get('ema14',          0) or 0), 2),
        'ema50':             round(float(s.get('ema50',          0) or 0), 2),
        'emaGapPct':         round(float(s.get('emaGapPct',      0) or 0), 3),
        'ema14Rising':       bool(s.get('ema14Rising')),
        'ema14RisingFast':   bool(s.get('ema14RisingFast')),
        'emaCrossDays':      s.get('emaCrossDays'),
        # ── Stage & signal flags ──────────────────────────────────
        'stage':             s.get('stage',    'none'),
        'vpbScore':          s.get('vpbScore',    0) or 0,
        'vpbDetail':         s.get('vpbDetail', 'none'),
        'crossScore':        s.get('crossScore',  0) or 0,
        'emaPreCross':       bool(s.get('emaPreCross')),
        'emaCross':          bool(s.get('emaCross')),
        'emaPostCross':      bool(s.get('emaPostCross')),
        'emaPullback':       bool(s.get('emaPullback')),
        'emaTrend':          bool(s.get('emaTrend')),
        'volConfirm':        bool(s.get('volConfirm')),
        'priceCoiling':      bool(s.get('priceCoiling')),
        'volShrinking':      bool(s.get('volShrinking')),
        # ── Liquidity metrics ─────────────────────────────────────
        'dailyVol':          round(float(s.get('dailyVol',  0) or 0), 2),   # traded value (Cr)
        'avg20Vol':          round(float(s.get('avg20Vol',  0) or 0), 1),   # 20d avg vol (shares)
        'mcap':              s.get('mcap',     0) or 0,                     # market cap (Cr)
        'turnoverRatio':     round(                                          # dailyVol / mcap %
            float(s.get('dailyVol', 0) or 0) /
            max(float(s.get('mcap', 1) or 1), 1) * 100, 4),
        # ── Fundamentals ──────────────────────────────────────────
        'pe':                round(float(s.get('pe',          0) or 0), 1),
        'sectorMedianPe':    s.get('sectorMedianPe', 0) or 0,
        'peVsSector':        s.get('peVsSector',     0) or 0,
        'debtEq':            s.get('debtEq'),                               # D/E ratio
        'roe':               s.get('roe'),                                  # Return on equity
        'promoterHolding':   s.get('promoterHolding', 0) or 0,
        'pledging':          s.get('pledging',        0) or 0,
        # ── Price levels & range metrics ──────────────────────────
        'wk38High':          s.get('wk38High',     0) or 0,
        'wk38Low':           s.get('wk38Low',      0) or 0,
        'wk52High':          s.get('wk52High',     0) or 0,
        'wk52Low':           s.get('wk52Low',      0) or 0,
        'pctFrom38High':     s.get('pctFrom38High',0) or 0,
        'pctFrom38Low':      s.get('pctFrom38Low', 0) or 0,
        'ath':               s.get('ath',          0) or 0,
        'near38High':        bool(s.get('near38High')),
        'near52High':        bool(s.get('near52High')),
        'golden':            bool(s.get('golden')),
        # ── Target & upside ───────────────────────────────────────
        'mmTarget':          s.get('mmTarget',    0) or 0,
        'targetPrice':       s.get('targetPrice', 0) or 0,
        'upsidePct':         s.get('upsidePct',   0) or 0,
        'upsideRs':          s.get('upsideRs',    0) or 0,
        # ── Support / Resistance ──────────────────────────────────
        'sr_support':        s.get('srSupport',    []),
        'sr_resistance':     s.get('srResistance', []),
    }


def update_prices(signals, stocks, nifty_price, mode='eod', trading_date=None):
    """
    Append today's full snapshot to all open signals within tracking window.
    - prices{}         — kept for frontend matrix view (reads sig.prices[date])
    - nifty_prices{}   — kept for NIFTY alpha calculation
    - daily_snapshots{} — comprehensive per-day record of ALL data points
    Daily snapshots only written on EOD (not intraday price-refresh).
    trading_date: the date of the market data (from stocks.json), not system clock.
    """
    today = trading_date or today_str()
    stock_map = {s.get('ticker', ''): s for s in stocks}
    updated = 0

    for sig in signals:
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
            # Backfill nifty_d0 if missing (e.g. signals logged by legacy compute.py)
            if not sig.get('nifty_d0'):
                day0 = sig.get('day0', '')
                sig['nifty_d0'] = sig['nifty_prices'].get(day0, round(float(nifty_price), 2))

        # Full daily snapshot — EOD only (intraday prices are noisy for technicals)
        if mode == 'eod':
            snapshot = build_daily_snapshot(s, nifty_price)
            sig.setdefault('daily_snapshots', {})[today] = snapshot

    print(f'[EVAL] Snapshots updated: {updated} open signals ({mode})')
    return updated


def day_n_for(sig, date_str):
    """Return the Nth trading day index (0-based) for date_str within this signal's prices dict."""
    sorted_dates = sorted(sig.get('prices', {}).keys())
    try:
        return sorted_dates.index(date_str)
    except ValueError:
        # Date not yet in prices — approximate from day0
        try:
            d0 = sorted_dates[0] if sorted_dates else sig.get('day0', date_str)
            delta = (datetime.date.fromisoformat(date_str) - datetime.date.fromisoformat(d0)).days
            return max(0, delta)
        except Exception:
            return 0


def update_stage_history(signals, stocks, trading_date=None):
    """
    Append today's stage to stage_history for open signals if stage changed.
    Only records transitions (deduped — only appends when stage differs from last entry).
    """
    today = trading_date or today_str()
    stage_map = {s.get('ticker', ''): s.get('stage', 'none') for s in stocks}
    updated = 0

    for sig in signals:
        if sig.get('outcome') in ('WIN', 'LOSS', 'EXPIRED'):
            continue
        ticker = sig.get('ticker', '')
        current_stage = stage_map.get(ticker)
        if not current_stage:
            continue
        hist = sig.setdefault('stage_history', [])
        # Only append if stage changed from last recorded entry
        if not hist or hist[-1].get('stage') != current_stage:
            hist.append({'date': today, 'stage': current_stage, 'day_n': day_n_for(sig, today)})
            updated += 1

    print(f'[EVAL] Stage history: {updated} transitions recorded')
    return updated


def check_retriggers(signals, eligible_tickers, trading_date=None):
    """
    Record today as a retrigger date for OPEN signals in today's top-3 selection.
    eligible_tickers: set returned by update_signal_tiers() — today's top-3 per phase + all-tab top-3.
    Golden line = stock appeared in top-3 of any phase (or all-tab) on this EOD run.
    """
    if not eligible_tickers:
        return set()

    today = trading_date or today_str()

    # Most recent open signal per ticker
    open_signals = {}
    for sig in signals:
        if sig.get('outcome') == 'OPEN':
            t = sig.get('ticker', '')
            prev = open_signals.get(t)
            if not prev or sig.get('day0', '') > prev.get('day0', ''):
                open_signals[t] = sig

    retriggered = set()
    for ticker in eligible_tickers:
        existing = open_signals.get(ticker)
        if not existing:
            continue
        rdates = existing.setdefault('retrigger_dates', [])
        existing_dates = [r['date'] if isinstance(r, dict) else r for r in rdates]
        if today not in existing_dates:
            rdates.append({'date': today, 'day_n': day_n_for(existing, today)})
            retriggered.add(ticker)

    if retriggered:
        print(f'[EVAL] Re-triggers recorded: {len(retriggered)}')
    return retriggered


def _rsi_score(r):
    """Mirror of scanner JS rsiScore() — max 12."""
    if r is None: return 0
    if 45 <= r <= 58: return 12
    if 58 < r <= 65: return 7
    if 40 <= r < 45:  return 4
    if 65 < r <= 72:  return 2
    return 0

def _adx_score(a):
    """Mirror of scanner JS adxScore() — max 10."""
    if a is None: return 0
    if 20 <= a <= 35: return 10
    if 15 <= a < 20:  return 5
    if a > 35:        return 4
    return 0

def _t_score(s):
    """Recomputed T score matching scanner render-time formula (not stored tScore)."""
    return _rsi_score(s.get('rsi')) + _adx_score(s.get('adx'))

def _stage_score(s):
    """Score used in scanner's phase tab: F + recomputed T (no signal score)."""
    return (s.get('fScore', 0) or 0) + _t_score(s)

def _total_score(s):
    """Full score matching scanner ALL tab: F + recomputed T + signal score."""
    return _stage_score(s) + calc_sig_score(s)


def backfill_tiers(signals):
    """
    Retroactively assign 'tier' to signals that are missing it.
    Within each (day0, stage) group, top 3 by score → 'signal', rest → 'baseline'.
    'none' stage → always 'baseline'.
    Called at the start of run_eod() to migrate existing signals.
    """
    needs_tier = [s for s in signals if 'tier' not in s]
    if not needs_tier:
        return 0

    groups = defaultdict(list)
    for s in needs_tier:
        groups[(s.get('day0', ''), s.get('stage', 'none'))].append(s)

    updated = 0
    for (d0, stage), grp in groups.items():
        if stage == 'none':
            for s in grp:
                s['tier'] = 'baseline'
                updated += 1
        else:
            # Phase-tab sort: F + recomputed T desc, upsidePct_d0 desc, mcap desc
            for i, s in enumerate(sorted(grp, key=lambda x: (
                    -(_rsi_score(x.get('rsi')) + _adx_score(x.get('adx')) + (x.get('fScore') or 0)),
                    -(x.get('upsidePct_d0') or 0),
                    -(x.get('mcap') or 0)))):
                s['tier'] = 'signal' if i < 3 else 'baseline'
                updated += 1

    if updated:
        print(f'[EVAL] Backfilled tier for {updated} signals')
    return updated


def log_new_signals(signals, stocks, trading_date=None):
    """
    Log stocks with tier assignment:
      tier='signal'   — top 3 per stage (by total score) + top 3 all-tab score not already selected
      tier='baseline' — all remaining stocks (for LIFECYCLE analysis)

    D0 reset rules:
    - WIN or LOSS: new D0 = outcome_date; price_d0 = outcome_price
    - EXPIRED:     new D0 = outcome_date + 1 calendar day (the 61st day)
    - First entry: D0 = today
    """
    today = trading_date or today_str()
    open_tickers = {sig.get('ticker', '') for sig in signals if not sig.get('outcome') or sig.get('outcome') == 'OPEN'}

    # Most-recent resolved signal per ticker (for D0 reset)
    resolved_latest = {}
    for sig in signals:
        if sig.get('outcome') in ('WIN', 'LOSS', 'EXPIRED'):
            t = sig.get('ticker', '')
            prev = resolved_latest.get(t)
            if not prev or sig.get('day0', '') > prev.get('day0', ''):
                resolved_latest[t] = sig

    # Precompute cycle counts (how many existing signals per ticker)
    cycle_count = defaultdict(int)
    for sig in signals:
        cycle_count[sig.get('ticker', '')] += 1

    # ── Tier selection: which stocks get tier='signal' ────────────────────────
    # avail = stocks without an open signal (candidates for new logging this run)
    avail = [s for s in stocks if s.get('ticker') and s.get('ticker') not in open_tickers]

    # Top 3 per stage — computed from ALL stocks globally, same as scanner ranking.
    # A newly-logged stock only earns tier='signal' if it would rank in the global
    # top 3 for its stage; comparing only within avail would produce wrong results
    # when avail is tiny (e.g. only a few stocks have resolved their prior cycle).
    signal_tickers = set()
    all_stage_groups = defaultdict(list)
    for s in stocks:       # ALL stocks, not just avail
        stage = s.get('stage', 'none')
        if stage != 'none':
            all_stage_groups[stage].append(s)

    def _phase_key(s):
        # Matches scanner phase-tab sort: F+T only desc, upsidePct desc, mcap desc
        return (-_stage_score(s), -(s.get('upsidePct') or 0), -(s.get('mcap') or 0))

    def _all_key(s):
        # Matches scanner ALL-tab sort: F+T+sig desc, upsidePct desc, mcap desc
        return (-_total_score(s), -(s.get('upsidePct') or 0), -(s.get('mcap') or 0))

    for stage, cands in all_stage_groups.items():
        for s in sorted(cands, key=_phase_key)[:3]:
            signal_tickers.add(s.get('ticker', ''))

    # All-tab top 3: from ALL non-none stocks; add any not already in signal_tickers
    all_non_none = [s for s in stocks if s.get('stage', 'none') != 'none']
    all_top3 = sorted(all_non_none, key=_all_key)[:3]
    extra = 0
    for s in all_top3:
        t = s.get('ticker', '')
        if t and t not in signal_tickers:
            signal_tickers.add(t)
            extra += 1

    print(f'[EVAL] Signal tier: {len(signal_tickers)} stocks '
          f'({len(signal_tickers) - extra} from stage top-3 + {extra} all-tab extras)')

    new_count = 0
    for s in stocks:
        stage = s.get('stage', 'none')
        ticker = s.get('ticker', '')
        if not ticker:
            continue
        if ticker in open_tickers:
            continue  # still active — no new cycle yet

        # Determine new D0 and price_d0 for this cycle
        prior = resolved_latest.get(ticker)
        if prior and prior.get('outcome') in ('WIN', 'LOSS'):
            # Reset: D0 = the day WIN/LOSS was hit; price = outcome_price
            new_d0       = prior.get('outcome_date', today)
            new_price_d0 = prior.get('outcome_price') or s.get('price', 0)
        elif prior and prior.get('outcome') == 'EXPIRED':
            # Reset: D0 = 61st day (outcome_date + 1 calendar day)
            try:
                exp_date = datetime.date.fromisoformat(prior['outcome_date'])
                new_d0   = (exp_date + datetime.timedelta(days=1)).isoformat()
            except Exception:
                new_d0 = today
            new_price_d0 = s.get('price', 0)
        else:
            new_d0       = today
            new_price_d0 = s.get('price', 0)

        sig_score = calc_sig_score(s)
        total = (s.get('fScore', 0) or 0) + (s.get('tScore', 0) or 0) + sig_score

        cycle = cycle_count[ticker] + 1   # 1-based cycle number for this ticker
        tier  = 'signal' if ticker in signal_tickers else 'baseline'

        entry = {
            # ── Identity ──────────────────────────────────────────
            'id':              f'{ticker}_{new_d0}_c{cycle}',
            'ticker':          ticker,
            'name':            s.get('name', ticker),
            'sector':          s.get('sector', 'Others'),
            'board':           s.get('board', 'main'),
            'day0':            new_d0,
            'cycle':           cycle,
            'tier':            tier,   # 'signal' → STOCK-WISE/ANALYTICS | 'baseline' → LIFECYCLE only
            'stage':           stage,
            'source':          'eval_worker',
            # ── Scores at entry ───────────────────────────────────
            'score':           total,
            'fScore':          s.get('fScore',   0) or 0,
            'tScore':          s.get('tScore',   0) or 0,
            'sigScore':        sig_score,
            'cScore':          s.get('cScore',   0) or 0,
            'ctScore':         s.get('ctScore',  0) or 0,
            # ── Technicals at entry ───────────────────────────────
            'rsi':             round(s.get('rsi',  0) or 0, 1),
            'adx':             round(s.get('adx',  0) or 0, 1),
            'macd':            s.get('macd',        0) or 0,
            # ── Fundamentals at entry ─────────────────────────────
            'pe':              round(s.get('pe',   0) or 0, 1),
            'mcap':            s.get('mcap',        0) or 0,
            'debtEq':          s.get('debtEq'),
            'roe':             s.get('roe'),
            'deFlag':          s.get('deFlag'),
            'roeFlag':         s.get('roeFlag'),
            'promoterHolding': s.get('promoterHolding', 0) or 0,
            'pledging':        s.get('pledging',        0) or 0,
            # ── EMA levels at entry ───────────────────────────────
            'ema14':           round(float(s.get('ema14',         0) or 0), 2),
            'ema50':           round(float(s.get('ema50',         0) or 0), 2),
            'emaGapPct':       round(float(s.get('emaGapPct',     0) or 0), 3),
            'ema14Rising':     bool(s.get('ema14Rising')),
            'ema14RisingFast': bool(s.get('ema14RisingFast')),
            'emaCrossDays':    s.get('emaCrossDays'),
            # ── Signal flags at entry ─────────────────────────────
            'vpbScore':        s.get('vpbScore',    0) or 0,
            'vpbDetail':       s.get('vpbDetail', 'none'),
            'crossScore':      s.get('crossScore',  0) or 0,
            'emaPreCross':     bool(s.get('emaPreCross')),
            'emaCross':        bool(s.get('emaCross')),
            'emaPostCross':    bool(s.get('emaPostCross')),
            'emaPullback':     bool(s.get('emaPullback')),
            'emaTrend':        bool(s.get('emaTrend')),
            'volConfirm':      bool(s.get('volConfirm')),
            'priceCoiling':    bool(s.get('priceCoiling')),
            'volShrinking':    bool(s.get('volShrinking')),
            # ── Liquidity at entry ────────────────────────────────
            'avg20Vol':         round(float(s.get('avg20Vol', 0) or 0), 1),
            'turnoverRatio_d0': round(
                float(s.get('dailyVol', 0) or 0) /
                max(float(s.get('mcap', 1) or 1), 1) * 100, 4),
            # ── Price levels at entry ─────────────────────────────
            'wk38High':        s.get('wk38High',     0) or 0,
            'wk38Low':         s.get('wk38Low',      0) or 0,
            'wk52High':        s.get('wk52High',     0) or 0,
            'wk52Low':         s.get('wk52Low',      0) or 0,
            'pctFrom38High':   s.get('pctFrom38High',0) or 0,
            'pctFrom38Low':    s.get('pctFrom38Low', 0) or 0,
            'ath':             s.get('ath',          0) or 0,
            'near38High':      bool(s.get('near38High')),
            'near52High':      bool(s.get('near52High')),
            'golden':          bool(s.get('golden')),
            # ── Target & upside at entry ──────────────────────────
            'mmTarget':        s.get('mmTarget',    0) or 0,
            'targetPrice':     s.get('targetPrice', 0) or 0,
            'upsideRs':        s.get('upsideRs',    0) or 0,
            # ── Price & volume at entry ───────────────────────────
            'price_d0':        new_price_d0,
            'volume_d0':       s.get('dailyVol',       0) or 0,
            'avg_volume_d0':   s.get('avg20Vol',       0) or 0,
            'upsidePct_d0':    s.get('upsidePct',      0) or 0,
            # ── Phase indicators at entry ─────────────────────────
            'volRatio_d0':     round(float(s.get('volRatio',      0) or 0), 3),
            'closePos_d0':     round(float(s.get('closePos',      0) or 0), 3),
            'vpbRangeHeight':  round(float(s.get('vpbRangeHeight',0) or 0), 2),
            # ── Sectoral PE at entry ──────────────────────────────
            'sectorMedianPe':  s.get('sectorMedianPe', 0) or 0,
            'peVsSector':      s.get('peVsSector',     0) or 0,
            # ── Support / Resistance at entry ─────────────────────
            'sr_support':      s.get('srSupport',    []),
            'sr_resistance':   s.get('srResistance', []),
            # ── Benchmark at entry ────────────────────────────────
            'nifty_d0':        0,   # filled by update_prices on D0
            # ── Backward stage history ────────────────────────────
            'prev_stages':     s.get('prevStages', {}),
            # ── Growing series (appended daily by update_prices) ──
            'prices':          {new_d0: new_price_d0} if new_price_d0 else {},
            'nifty_prices':    {},
            'daily_snapshots': {new_d0: build_daily_snapshot(s, 0, sig_score)},
            'stage_history':   [{'date': new_d0, 'stage': stage, 'day_n': 0}],
            'retrigger_dates': [],
            'outcome':         'OPEN',
            'outcome_day':     None,
            'outcome_date':    None,
            'outcome_price':   None,
            'outcome_ret':     None,
        }
        signals.append(entry)
        open_tickers.add(ticker)    # prevent same ticker appearing twice if MCap changes mid-loop
        cycle_count[ticker] += 1   # update count so duplicate tickers in stocks list get unique ids
        new_count += 1

    print(f'[EVAL] New signals logged: {new_count} (new cycles for stocks without an open signal)')
    return new_count


def update_signal_tiers(signals, stocks, trading_date=None):
    """
    Promote today's top-3 per phase to tier='signal'. NEVER demotes.

    Signal tier is cumulative within a cycle:
      - A stock promoted on any EOD day stays signal until WIN/LOSS/EXPIRED.
      - Stocks already signal keep their tier regardless of today's ranking.

    Selection uses TODAY's stage and scores from stocks.json (current scan data)
    so that stocks which have moved phases since D0 are ranked correctly.

    Selection criteria:
      - Top 3 OPEN signals per TODAY's phase, sorted by F+T desc → upsidePct desc → mcap desc
      - All-tab top 3 OPEN signals by F+T+S desc (same tiebreaks) — added if not already selected
    """
    stock_map = {s.get('ticker', ''): s for s in stocks}

    # Pair each open signal with today's stock record
    open_pairs = []
    for sig in signals:
        if sig.get('outcome', 'OPEN') != 'OPEN':
            continue
        ticker = sig.get('ticker', '')
        s = stock_map.get(ticker)
        if s and s.get('price'):
            open_pairs.append((sig, s))

    # Group by TODAY's stage from stocks.json
    stage_groups = defaultdict(list)
    for sig, s in open_pairs:
        stage = s.get('stage', 'none')
        if stage != 'none':
            stage_groups[stage].append((sig, s))

    def _ph_key(pair):
        s = pair[1]
        return (-_stage_score(s), -(s.get('upsidePct') or 0), -(s.get('mcap') or 0))

    def _all_key(pair):
        s = pair[1]
        return (-_total_score(s), -(s.get('upsidePct') or 0), -(s.get('mcap') or 0))

    newly_eligible = set()

    # Top 3 per phase
    for stage, pairs in stage_groups.items():
        for sig, s in sorted(pairs, key=_ph_key)[:3]:
            newly_eligible.add(sig.get('ticker', ''))

    # All-tab top 3
    extra = 0
    for sig, s in sorted(open_pairs, key=_all_key)[:3]:
        t = sig.get('ticker', '')
        if t and t not in newly_eligible:
            newly_eligible.add(t)
            extra += 1

    # Promote only — never demote
    today = trading_date or today_str()
    promoted = 0
    for sig in signals:
        if sig.get('outcome') in ('WIN', 'LOSS', 'EXPIRED'):
            continue
        if sig.get('ticker', '') in newly_eligible and sig.get('tier') != 'signal':
            sig['tier'] = 'signal'
            sig['tier_date'] = today   # date this stock first appeared in Stock-Wise
            promoted += 1

    total_signal = sum(1 for s in signals
                       if s.get('tier') == 'signal' and s.get('outcome', 'OPEN') == 'OPEN')
    phase_count = len(newly_eligible) - extra
    print(f'[EVAL] Tier refresh: {len(newly_eligible)} newly eligible '
          f'({phase_count} from phase top-3 + {extra} all-tab extras) | '
          f'promoted: {promoted} | total signal-tier open: {total_signal}')
    return newly_eligible


def recompute_all_outcomes(signals):
    """Recompute WIN/LOSS/EXPIRED for all open signals. Already-resolved signals are skipped."""
    for sig in signals:
        if sig.get('outcome') not in ('WIN', 'LOSS', 'EXPIRED'):
            compute_outcome(sig)


# ── Entry points ──────────────────────────────────────────────────────────────

def run_eod():
    """
    Full EOD update:
    1. Load current scan results from stocks.json
    2. Backfill tiers on legacy signals missing the field
    3. Log new signals (new cycles for stocks without an open signal)
    4. Update signal tiers — re-rank ALL open signals; top-3 per phase → 'signal', rest → 'baseline'
    5. Check retriggers — golden line for stocks in today's top-3 (uses eligible set from step 4)
    6. Update stage history
    7. Update prices (EOD close)
    8. Recompute outcomes
    9. Save
    """
    print('[EVAL] === EOD run starting ===')
    stocks, nifty, trading_date = load_stocks()
    if not stocks:
        print('[EVAL] No stocks data — aborting')
        return
    # Use --date override if provided, otherwise use trading date from stocks.json
    trading_date = today_str() if _DATE_OVERRIDE else trading_date
    print(f'[EVAL] Trading date: {trading_date}')

    # Skip if today (system date) is not a trading day — weekends/holidays produce no new market data.
    # Exception: --date override = explicit backfill, always allowed.
    if not _DATE_OVERRIDE and not is_trading_day(datetime.date.today().isoformat()):
        print(f'[EVAL] Today ({datetime.date.today().isoformat()}) is not a trading day — skipping update.')
        return

    signals = load_signals()
    print(f'[EVAL] Loaded {len(signals)} existing signals')

    backfill_tiers(signals)               # assign tier to any signals logged before tier field existed
    log_new_signals(signals, stocks, trading_date=trading_date)
    eligible = update_signal_tiers(signals, stocks, trading_date=trading_date)
    check_retriggers(signals, eligible, trading_date=trading_date)
    update_stage_history(signals, stocks, trading_date=trading_date)
    update_prices(signals, stocks, nifty, mode='eod', trading_date=trading_date)
    recompute_all_outcomes(signals)
    save_signals(signals)

    open_count    = sum(1 for s in signals if s.get('outcome') == 'OPEN')
    win_count     = sum(1 for s in signals if s.get('outcome') == 'WIN')
    loss_count    = sum(1 for s in signals if s.get('outcome') == 'LOSS')
    expired_count = sum(1 for s in signals if s.get('outcome') == 'EXPIRED')
    print(f'[EVAL] Done — total: {len(signals)} | open: {open_count} | WIN: {win_count} | LOSS: {loss_count} | EXPIRED: {expired_count}')


def run_price_refresh():
    """
    Intraday price-only update:
    1. Load current prices from stocks.json
    2. Append today's price to open signals within tracking window
    3. Recompute outcomes
    4. Save
    """
    print('[EVAL] === Price refresh starting ===')
    stocks, nifty, trading_date = load_stocks()
    if not stocks:
        print('[EVAL] No stocks data — aborting')
        return

    if not is_trading_day(datetime.date.today().isoformat()):
        print(f'[EVAL] Today ({datetime.date.today().isoformat()}) is not a trading day — skipping price refresh.')
        return

    signals = load_signals()
    update_prices(signals, stocks, nifty, mode='price-refresh', trading_date=trading_date)
    recompute_all_outcomes(signals)
    save_signals(signals)
    print('[EVAL] Price refresh done')


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    import re as _re
    # Patch date override early — before any function that calls today_str()
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
    parser.add_argument('--date',          default=None, help='Override today date (YYYY-MM-DD) for backfill runs')
    args = parser.parse_args()

    if args.eod:
        run_eod()
    elif getattr(args, 'price_refresh', False):
        run_price_refresh()
    else:
        parser.print_help()
