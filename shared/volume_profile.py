"""
shared/volume_profile.py
=========================
Volume Profile data fetch, resampling, profile construction, and
decision-tree analysis per docs/VOLUME_PROFILE_FRAMEWORK.md.

Used by:
  - volume_profile.py     (CLI research script)
  - server.py              (/api/volume-profile/<ticker>)

All levels (VAH/POC/VAL/HVN/LVN) come from the WEEKLY profile only,
per Rule 1. Daily and 4H data are used for candle-signal analysis
(§15-22) against those weekly levels.
"""

import datetime
import pandas as pd
import numpy as np

DAILY_YEARS       = 3     # fetched range, used to build the weekly VP (Rule 1)
DAILY_DISPLAY_BARS = 250  # ~1 year of trading days shown on the DAILY chart
INTRADAY_DAYS     = 180   # ~6 months; Kite caps 60minute at 400 days/call
VALUE_AREA_PCT    = 0.70
NEAR_LEVEL_PCT = 0.005  # Rule 4A: within 0.5% of a weekly level = "at" the level
VOL_AVG_PERIOD = 20


# ════════════════════════════════════════════════════════════════════
# FETCH
# ════════════════════════════════════════════════════════════════════
def fetch_candles(kite, token, interval, from_dt, to_dt):
    records = kite.historical_data(token, from_date=from_dt, to_date=to_dt, interval=interval)
    if not records:
        return pd.DataFrame(columns=['open', 'high', 'low', 'close', 'volume'])
    df = pd.DataFrame(records)
    df['date'] = pd.to_datetime(df['date'])
    df = df.set_index('date').sort_index()
    return df[['open', 'high', 'low', 'close', 'volume']]


def fetch_all(kite, token, today=None):
    """Fetch daily (3yr), weekly (resampled), and 4H (NSE-aligned, 6mo) candles."""
    if today is None:
        today = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5, minutes=30))).date()

    daily_from = today - datetime.timedelta(days=365 * DAILY_YEARS)
    daily = fetch_candles(kite, token, 'day', daily_from, today)

    weekly = resample_ohlcv(daily, 'W')

    intraday_from = today - datetime.timedelta(days=INTRADAY_DAYS)
    intraday = fetch_candles(kite, token, '60minute', intraday_from, today)
    fourh = resample_4h_nse(intraday)
    fourh = fourh[fourh['volume'] > 0]

    return daily, weekly, fourh


# ════════════════════════════════════════════════════════════════════
# RESAMPLE
# ════════════════════════════════════════════════════════════════════
def resample_ohlcv(df, rule):
    agg = {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'}
    out = df.resample(rule).agg(agg).dropna(subset=['open'])
    return out


def resample_4h_nse(df):
    """Resample 60min candles into NSE-session-aligned 4H bars.

    NSE session is 09:15-15:30 (375 min). Anchoring 4H (240 min) bars at
    09:15 gives 2 bars/day: 09:15-13:15 and 13:15-15:30 (the second is a
    shorter 2h15m bar since the session ends before 4H completes).
    """
    if df.empty:
        return pd.DataFrame(columns=['open', 'high', 'low', 'close', 'volume'])
    agg = {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'}
    out = []
    for _, day_df in df.groupby(df.index.date):
        day_df = day_df.sort_index()
        origin = day_df.index[0]  # first bar of the session, e.g. 09:15
        grouped = day_df.resample('4h', origin=origin, closed='left', label='left').agg(agg)
        out.append(grouped.dropna(subset=['open']))
    if not out:
        return pd.DataFrame(columns=['open', 'high', 'low', 'close', 'volume'])
    return pd.concat(out).sort_index()


# ════════════════════════════════════════════════════════════════════
# VOLUME AVERAGES (§22 — timeframe-specific N-period average)
# ════════════════════════════════════════════════════════════════════
def add_volume_avg(df, period=VOL_AVG_PERIOD):
    """Add rolling N-period average volume and a ratio column (vol / avg)."""
    out = df.copy()
    out['vol_avg'] = out['volume'].rolling(period).mean()
    out['vol_ratio'] = out['volume'] / out['vol_avg']
    return out


# ════════════════════════════════════════════════════════════════════
# VOLUME PROFILE (weekly only — Rule 1)
# ════════════════════════════════════════════════════════════════════
def build_volume_profile(df, bins=50):
    """Bin each bar's volume across its [low, high] range and return a
    price-bucket histogram plus VAH/POC/VAL and HVN/LVN zones.
    """
    lo, hi = df['low'].min(), df['high'].max()
    edges = np.linspace(lo, hi, bins + 1)
    vol_per_bin = np.zeros(bins)

    for _, row in df.iterrows():
        row_lo, row_hi, vol = row['low'], row['high'], row['volume']
        if row_hi <= row_lo or vol <= 0:
            continue
        start_bin = np.searchsorted(edges, row_lo, side='right') - 1
        end_bin   = np.searchsorted(edges, row_hi, side='right') - 1
        start_bin = max(0, min(start_bin, bins - 1))
        end_bin   = max(0, min(end_bin, bins - 1))
        span = end_bin - start_bin + 1
        vol_per_bin[start_bin:end_bin + 1] += vol / span

    centers = (edges[:-1] + edges[1:]) / 2

    poc_idx = int(np.argmax(vol_per_bin))
    poc = centers[poc_idx]

    total_vol = vol_per_bin.sum()
    cum_vol = vol_per_bin[poc_idx]
    lo_idx, hi_idx = poc_idx, poc_idx
    while cum_vol < total_vol * VALUE_AREA_PCT and (lo_idx > 0 or hi_idx < bins - 1):
        next_lo = vol_per_bin[lo_idx - 1] if lo_idx > 0 else -1
        next_hi = vol_per_bin[hi_idx + 1] if hi_idx < bins - 1 else -1
        if next_hi >= next_lo:
            hi_idx += 1
            cum_vol += vol_per_bin[hi_idx]
        else:
            lo_idx -= 1
            cum_vol += vol_per_bin[lo_idx]

    vah = edges[hi_idx + 1]
    val = edges[lo_idx]

    order = np.argsort(vol_per_bin)
    hvn_idx = order[::-1][:5]
    nonzero = [i for i in order if vol_per_bin[i] > 0]
    lvn_idx = nonzero[:5]

    return {
        'edges': edges.tolist(),
        'centers': centers.tolist(),
        'vol_per_bin': vol_per_bin.tolist(),
        'poc': float(poc),
        'vah': float(vah),
        'val': float(val),
        'hvn': sorted([(float(centers[i]), float(vol_per_bin[i])) for i in hvn_idx], key=lambda x: -x[1]),
        'lvn': sorted([(float(centers[i]), float(vol_per_bin[i])) for i in lvn_idx], key=lambda x: x[1]),
    }


# ════════════════════════════════════════════════════════════════════
# DECISION-TREE ANALYSIS (§27, §15-22)
# ════════════════════════════════════════════════════════════════════
def _near(price, level):
    if level == 0:
        return False
    return abs(price - level) / level <= NEAR_LEVEL_PCT


def classify_position(price, vp):
    """Classify current price relative to weekly VP levels (Rule 4/4A/4B/4C).

    Returns dict with keys: zone, nearest_level_name, nearest_level_price.
    zone is one of:
      'at_val', 'at_poc', 'at_vah', 'below_val', 'above_vah',
      'inside_value_area', 'no_mans_land'
    """
    vah, poc, val = vp['vah'], vp['poc'], vp['val']

    # vp['hvn'] is a list of (price, volume) tuples
    level_candidates = [('VAL', val), ('POC', poc), ('VAH', vah)]
    for hvn_price, _ in vp.get('hvn', []):
        level_candidates.append(('HVN', hvn_price))

    # find nearest level by % distance
    nearest_name, nearest_price, nearest_dist = None, None, None
    for name, lvl in level_candidates:
        if lvl == 0:
            continue
        dist = abs(price - lvl) / lvl
        if nearest_dist is None or dist < nearest_dist:
            nearest_name, nearest_price, nearest_dist = name, lvl, dist

    if price < val and not _near(price, val):
        zone = 'below_val'
    elif price > vah and not _near(price, vah):
        zone = 'above_vah'
    elif _near(price, val):
        zone = 'at_val'
    elif _near(price, poc):
        zone = 'at_poc'
    elif _near(price, vah):
        zone = 'at_vah'
    elif any(_near(price, hvn_price) for hvn_price, _ in vp.get('hvn', [])):
        zone = 'at_hvn'
    elif val < price < vah:
        zone = 'inside_value_area'
    else:
        zone = 'no_mans_land'

    return {
        'zone': zone,
        'nearest_level_name': nearest_name,
        'nearest_level_price': nearest_price,
        'nearest_level_dist_pct': round(nearest_dist * 100, 2) if nearest_dist is not None else None,
    }


def classify_candle(prev_close, candle, level, vol_ratio, vol_threshold=1.0):
    """Classify a single candle's interaction with `level` per §15-17.

    Returns dict: { 'type': 'bounce'|'reclaim'|'retest_candidate'|'none',
                     'closes_above': bool, 'touches': bool,
                     'volume_ok': bool, 'valid_signal': bool }
    """
    o, h, l, c = candle['open'], candle['high'], candle['low'], candle['close']
    touches = l <= level <= h or (l <= level and h >= level)
    closes_above = c > level
    closes_at = abs(c - level) / level <= NEAR_LEVEL_PCT if level else False
    volume_ok = vol_ratio is not None and vol_ratio >= vol_threshold

    if prev_close is not None and prev_close < level and c > level:
        # crossed through from below
        ctype = 'reclaim'
    elif touches and closes_above:
        # came down to / sits at level and closed above -> bounce or retest
        ctype = 'bounce_or_retest'
    elif closes_at:
        ctype = 'indecision'
    else:
        ctype = 'none'

    valid_signal = (ctype in ('reclaim', 'bounce_or_retest')) and closes_above and volume_ok

    return {
        'type': ctype,
        'closes_above': bool(closes_above),
        'closes_at': bool(closes_at),
        'touches': bool(touches),
        'volume_ok': bool(volume_ok),
        'valid_signal': bool(valid_signal),
    }


def analyze_timeframe(df, vp, label, vol_threshold=1.0, lookback=1):
    """Analyze the most recent candle(s) of `df` against weekly VP levels.

    Returns a dict summarizing candle signals for VAH/POC/VAL/nearest HVN.
    """
    if df is None or df.empty or len(df) < VOL_AVG_PERIOD + 1:
        return {'label': label, 'available': False}

    dfa = add_volume_avg(df, VOL_AVG_PERIOD).dropna(subset=['vol_avg'])
    if dfa.empty:
        return {'label': label, 'available': False}

    last = dfa.iloc[-1]
    prev_close = float(dfa.iloc[-2]['close']) if len(dfa) >= 2 else None
    vol_ratio = float(last['vol_ratio']) if pd.notna(last['vol_ratio']) else None

    candle = {'open': float(last['open']), 'high': float(last['high']),
              'low': float(last['low']), 'close': float(last['close'])}

    levels_to_check = {'VAH': vp['vah'], 'POC': vp['poc'], 'VAL': vp['val']}
    for i, (hvn_price, _) in enumerate(vp.get('hvn', [])[:3]):
        levels_to_check[f'HVN{i+1}'] = hvn_price

    signals = {}
    for name, level in levels_to_check.items():
        if _near(candle['close'], level) or _near(candle['high'], level) or _near(candle['low'], level) \
           or (prev_close is not None and ((prev_close < level <= candle['close']) or (prev_close > level >= candle['close']))):
            signals[name] = classify_candle(prev_close, candle, level, vol_ratio, vol_threshold)

    return {
        'label': label,
        'available': True,
        'last_close': float(last['close']),
        'last_date': str(dfa.index[-1]),
        'vol_ratio': round(vol_ratio, 2) if vol_ratio is not None else None,
        'vol_above_avg': bool(vol_ratio is not None and vol_ratio > 1),
        'vol_above_2x': bool(vol_ratio is not None and vol_ratio >= 2),
        'signals': signals,
    }


def build_analysis(daily, weekly, fourh, vp):
    """Top-level analysis: position vs weekly VP + per-timeframe candle signals.

    Mirrors §27 master decision tree and §30 checklist at a high level.
    """
    if daily is None or daily.empty:
        return {'error': 'no daily data'}

    last_close = float(daily['close'].iloc[-1])
    position = classify_position(last_close, vp)

    daily_analysis = analyze_timeframe(daily, vp, 'daily', vol_threshold=1.0)
    fourh_analysis = analyze_timeframe(fourh, vp, '4h', vol_threshold=1.0)

    # VAH breakout needs 2x volume (Rule 20) — re-check VAH specifically on daily
    vah_breakout = None
    if daily_analysis.get('available') and 'VAH' in daily_analysis['signals']:
        vah_sig = daily_analysis['signals']['VAH']
        vah_breakout = {
            **vah_sig,
            'meets_2x_volume': daily_analysis.get('vol_above_2x', False),
            'valid_breakout': vah_sig['closes_above'] and daily_analysis.get('vol_above_2x', False),
        }

    # Overall verdict based on zone (§27)
    zone = position['zone']
    verdict_map = {
        'below_val':         'Below weekly VAL — wait for Strategy 1D reclaim + retest',
        'at_val':            'At weekly VAL — watch for Strategy 1C bounce on 4H',
        'at_poc':            'At weekly POC — watch for Strategy 1A bounce on 4H (if approaching from above) or 1B reclaim (from below)',
        'at_vah':            'At weekly VAH — check Strategy 2 breakout conditions (2x volume required)',
        'at_hvn':            'At a weekly HVN — watch for Strategy 1A bounce on 4H',
        'inside_value_area': 'Inside value area — no immediate setup; monitor for approach to VAL/POC/VAH',
        'above_vah':         'Above weekly VAH (blue zone) — monitor for value shift / retest per §13',
        'no_mans_land':      'Beyond 0.5% of any weekly level — true no man\'s land, no setup (Rule 4C)',
    }

    return {
        'last_close': last_close,
        'position': position,
        'verdict': verdict_map.get(zone, 'Unclassified'),
        'daily': daily_analysis,
        '4h': fourh_analysis,
        'vah_breakout': vah_breakout,
    }


# ════════════════════════════════════════════════════════════════════
# SERIALIZATION HELPERS
# ════════════════════════════════════════════════════════════════════
def candles_to_list(df, limit=None):
    """Convert OHLCV DataFrame to list of dicts with ISO date strings."""
    if df is None or df.empty:
        return []
    out = df.tail(limit) if limit else df
    rows = []
    for ts, row in out.iterrows():
        rows.append({
            'date':   ts.isoformat(),
            'open':   round(float(row['open']), 2),
            'high':   round(float(row['high']), 2),
            'low':    round(float(row['low']), 2),
            'close':  round(float(row['close']), 2),
            'volume': int(row['volume']),
        })
    return rows
