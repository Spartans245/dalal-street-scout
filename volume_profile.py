"""
volume_profile.py — Volume Profile research script
====================================================
Standalone, one-off script to validate the Volume Profile approach
described in docs/VOLUME_PROFILE_FRAMEWORK.md.

Fetches from Kite Connect:
  - ~3 years of daily candles  -> resampled to WEEKLY for VP (VAH/POC/VAL/HVN/LVN)
  - ~6 months of 60-minute candles -> resampled to 4H candles

Computes a weekly Volume Profile (price-bucketed volume histogram) and
prints VAH / POC / VAL plus the top HVN and LVN zones.

Usage:
    python volume_profile.py RELIANCE
    python volume_profile.py RELIANCE --bins 40
    python volume_profile.py RELIANCE --full-vp        # print every VP bin
    python volume_profile.py RELIANCE --candles 20     # recent daily/4H candles + vol avg
"""

import sys, os, argparse, datetime

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import pandas as pd
import numpy as np

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from kite_worker import load_kite, load_instrument_map, get_ist

DAILY_YEARS   = 3
INTRADAY_DAYS = 180   # ~6 months; Kite caps 60minute at 400 days/call
VALUE_AREA_PCT = 0.70


# ════════════════════════════════════════════════════════════════════
# FETCH
# ════════════════════════════════════════════════════════════════════
def fetch_candles(kite, token, interval, from_dt, to_dt):
    records = kite.historical_data(token, from_date=from_dt, to_date=to_dt, interval=interval)
    if not records:
        return pd.DataFrame()
    df = pd.DataFrame(records)
    df['date'] = pd.to_datetime(df['date'])
    df = df.set_index('date').sort_index()
    return df[['open', 'high', 'low', 'close', 'volume']]


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
# VOLUME AVERAGES (§22 — timeframe-specific 20-period average)
# ════════════════════════════════════════════════════════════════════
def add_volume_avg(df, period=20):
    """Add rolling N-period average volume and a ratio column (vol / avg)."""
    out = df.copy()
    out['vol_avg20'] = out['volume'].rolling(period).mean()
    out['vol_ratio'] = out['volume'] / out['vol_avg20']
    return out


def print_candles(df, label, n):
    tail = df.tail(n)
    print()
    print('=' * 70)
    print(f'  {label} — last {len(tail)} candles (vol vs {20}-period avg)')
    print('=' * 70)
    print(f'  {"date":<20}{"open":>10}{"high":>10}{"low":>10}{"close":>10}{"volume":>14}{"x avg":>8}')
    for ts, row in tail.iterrows():
        ratio = row['vol_ratio']
        ratio_str = f'{ratio:.2f}x' if pd.notna(ratio) else '-'
        flag = '  >=2x' if pd.notna(ratio) and ratio >= 2 else ('   >avg' if pd.notna(ratio) and ratio > 1 else '')
        print(f'  {str(ts):<20}{row["open"]:>10.2f}{row["high"]:>10.2f}{row["low"]:>10.2f}'
              f'{row["close"]:>10.2f}{row["volume"]:>14,.0f}{ratio_str:>8}{flag}')


# ════════════════════════════════════════════════════════════════════
# VOLUME PROFILE
# ════════════════════════════════════════════════════════════════════
def build_volume_profile(df, bins=50):
    """Bin each daily bar's volume across its [low, high] range and
    return a price-bucket histogram plus VAH/POC/VAL and HVN/LVN zones.
    """
    lo, hi = df['low'].min(), df['high'].max()
    edges = np.linspace(lo, hi, bins + 1)
    vol_per_bin = np.zeros(bins)

    for _, row in df.iterrows():
        row_lo, row_hi, vol = row['low'], row['high'], row['volume']
        if row_hi <= row_lo or vol <= 0:
            continue
        # which bins does this bar's range overlap?
        start_bin = np.searchsorted(edges, row_lo, side='right') - 1
        end_bin   = np.searchsorted(edges, row_hi, side='right') - 1
        start_bin = max(0, min(start_bin, bins - 1))
        end_bin   = max(0, min(end_bin, bins - 1))
        span = end_bin - start_bin + 1
        # distribute volume evenly across overlapped bins
        vol_per_bin[start_bin:end_bin + 1] += vol / span

    centers = (edges[:-1] + edges[1:]) / 2

    # POC = bin with max volume
    poc_idx = int(np.argmax(vol_per_bin))
    poc = centers[poc_idx]

    # Value area: expand outward from POC until >= 70% of total volume
    total_vol = vol_per_bin.sum()
    included = {poc_idx}
    cum_vol = vol_per_bin[poc_idx]
    lo_idx, hi_idx = poc_idx, poc_idx
    while cum_vol < total_vol * VALUE_AREA_PCT and (lo_idx > 0 or hi_idx < bins - 1):
        next_lo = vol_per_bin[lo_idx - 1] if lo_idx > 0 else -1
        next_hi = vol_per_bin[hi_idx + 1] if hi_idx < bins - 1 else -1
        if next_hi >= next_lo:
            hi_idx += 1
            cum_vol += vol_per_bin[hi_idx]
            included.add(hi_idx)
        else:
            lo_idx -= 1
            cum_vol += vol_per_bin[lo_idx]
            included.add(lo_idx)

    vah = edges[hi_idx + 1]
    val = edges[lo_idx]

    # HVN/LVN: rank bins by volume, report top/bottom 5 (excluding zero-volume bins for LVN)
    order = np.argsort(vol_per_bin)
    hvn_idx = order[::-1][:5]
    nonzero = [i for i in order if vol_per_bin[i] > 0]
    lvn_idx = nonzero[:5]

    return {
        'edges': edges,
        'centers': centers,
        'vol_per_bin': vol_per_bin,
        'poc': poc,
        'vah': vah,
        'val': val,
        'hvn': sorted([(centers[i], vol_per_bin[i]) for i in hvn_idx], key=lambda x: -x[1]),
        'lvn': sorted([(centers[i], vol_per_bin[i]) for i in lvn_idx], key=lambda x: x[1]),
    }


# ════════════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('symbol', help='NSE trading symbol, e.g. RELIANCE')
    parser.add_argument('--bins', type=int, default=50, help='number of price buckets for VP (default 50)')
    parser.add_argument('--full-vp', action='store_true', help='print every weekly VP bin, not just top HVN/LVN')
    parser.add_argument('--candles', type=int, default=0, metavar='N',
                         help='print last N daily and 4H candles with volume vs 20-period average')
    args = parser.parse_args()
    symbol = args.symbol.upper()

    kite = load_kite()
    token_map = load_instrument_map(kite)
    token = token_map.get(symbol)
    if not token:
        print(f'ERROR: no instrument token found for {symbol}')
        sys.exit(1)

    today = get_ist().date()

    # ── Daily (3 years) ────────────────────────────────────────────
    daily_from = today - datetime.timedelta(days=365 * DAILY_YEARS)
    print(f'Fetching daily candles {daily_from} -> {today} ...')
    daily = fetch_candles(kite, token, 'day', daily_from, today)
    print(f'  {len(daily)} daily candles')

    # ── Weekly (resampled from daily) ──────────────────────────────
    weekly = resample_ohlcv(daily, 'W')
    print(f'  -> {len(weekly)} weekly candles')

    # ── 60-minute (6 months) ───────────────────────────────────────
    intraday_from = today - datetime.timedelta(days=INTRADAY_DAYS)
    print(f'Fetching 60min candles {intraday_from} -> {today} ...')
    intraday = fetch_candles(kite, token, '60minute', intraday_from, today)
    print(f'  {len(intraday)} 60-minute candles')

    # ── 4H (resampled from 60min, NSE session-aligned: 09:15-13:15, 13:15-15:30) ──
    fourh = resample_4h_nse(intraday)
    fourh = fourh[fourh['volume'] > 0]
    print(f'  -> {len(fourh)} 4H candles')

    # ── Weekly Volume Profile ───────────────────────────────────────
    vp = build_volume_profile(daily, bins=args.bins)

    print()
    print('=' * 55)
    print(f'  WEEKLY VOLUME PROFILE — {symbol}')
    print(f'  Range: {vp["edges"][0]:.2f} - {vp["edges"][-1]:.2f}  ({args.bins} bins)')
    print('=' * 55)
    print(f'  VAH (Value Area High) : {vp["vah"]:.2f}')
    print(f'  POC (Point of Control): {vp["poc"]:.2f}')
    print(f'  VAL (Value Area Low)  : {vp["val"]:.2f}')
    print()
    print('  Top HVN zones (price : volume):')
    for price, vol in vp['hvn']:
        print(f'    {price:9.2f} : {vol:,.0f}')
    print()
    print('  Top LVN zones (price : volume):')
    for price, vol in vp['lvn']:
        print(f'    {price:9.2f} : {vol:,.0f}')

    if args.full_vp:
        print()
        print('  Full VP histogram (price-bin center : volume):')
        max_vol = vp['vol_per_bin'].max()
        for center, vol in zip(vp['centers'], vp['vol_per_bin']):
            bar_len = int(40 * vol / max_vol) if max_vol else 0
            print(f'    {center:9.2f} : {vol:>14,.0f}  {"#" * bar_len}')

    if args.candles:
        daily_avg = add_volume_avg(daily, period=20)
        print_candles(daily_avg, f'DAILY — {symbol}', args.candles)
        fourh_avg = add_volume_avg(fourh, period=20)
        print_candles(fourh_avg, f'4H — {symbol}', args.candles)

    if not daily.empty:
        last_close = daily['close'].iloc[-1]
        print()
        print(f'  Last close: {last_close:.2f}')
        if last_close > vp['vah']:
            pos = 'ABOVE VAH (blue zone)'
        elif last_close < vp['val']:
            pos = 'BELOW VAL'
        else:
            pos = 'INSIDE value area'
        print(f'  Position vs VP: {pos}')


if __name__ == '__main__':
    main()
