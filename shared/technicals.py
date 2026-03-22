"""
shared/technicals.py
====================
Pure math functions — no Yahoo Finance, no NSE dependencies.
Shared by yf_worker.py and nse_worker.py.

Input:  pandas DataFrame with columns [Open, High, Low, Close, Volume]
        and DatetimeIndex (same format from yf.history() or Bhavcopy)
Output: dicts of computed signals/scores
"""
import json, math
import pandas as pd


# ════════════════════════════════════════════════════════════════════
# TECHNICAL INDICATORS
# ════════════════════════════════════════════════════════════════════
def calc_technicals(hist):
    if hist is None or len(hist) < 30:
        return None
    try:
        c = hist['Close'].ffill().dropna().values
        s = pd.Series(c)

        # RSI 14 — Wilder smoothing (industry standard, matches TradingView/Kite charts)
        delta = s.diff()
        gain  = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
        loss  = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
        rsi_s = 100 - 100/(1+gain/(loss+1e-10))
        rsi   = round(float(rsi_s.iloc[-1]), 1)
        if math.isnan(rsi): rsi = 50.0

        # MACD
        m_line = s.ewm(span=12).mean() - s.ewm(span=26).mean()
        sig    = m_line.ewm(span=9).mean()
        macd   = bool(m_line.iloc[-1] > sig.iloc[-1] and m_line.iloc[-2] <= sig.iloc[-2])

        # 14/50 EMA cross
        ema14 = s.ewm(span=14).mean()
        ema50 = s.ewm(span=50).mean()
        e14n  = float(ema14.iloc[-1])
        e50n  = float(ema50.iloc[-1])
        e14p  = float(ema14.iloc[-2]) if len(ema14)>1 else e14n
        e50p  = float(ema50.iloc[-2]) if len(ema50)>1 else e50n

        ema_cross = bool(e14n > e50n and e14p <= e50p)
        ema_cross_days_ago = None
        if ema_cross:
            ema_cross_days_ago = 1
        elif len(ema14) >= 4:
            for i in range(2, 6):
                if len(ema14) > i and \
                   float(ema14.iloc[-i]) > float(ema50.iloc[-i]) and \
                   float(ema14.iloc[-i-1]) <= float(ema50.iloc[-i-1]):
                    ema_cross = True
                    ema_cross_days_ago = i
                    break

        ema14_rising      = e14n > float(ema14.iloc[-5]) if len(ema14) >= 5 else False
        ema14_rising_fast = e14n > float(ema14.iloc[-3]) if len(ema14) >= 3 else False
        ema_trend         = bool(e14n > e50n and not ema_cross)

        # Pre-cross: 14 EMA below 50 EMA but gap < 0.5% of price and closing fast
        ema_pre_cross = False
        try:
            if not ema_cross and e14n < e50n and c[-1] > 0:
                gap_pct = (e50n - e14n) / c[-1] * 100
                ema_pre_cross = bool(gap_pct < 0.5 and ema14_rising_fast)
        except:
            pass

        # Post-cross: just crossed (1-2 days ago) and lines still in close proximity
        ema_post_cross = False
        try:
            if ema_cross and ema_cross_days_ago and ema_cross_days_ago <= 2 and c[-1] > 0:
                prox_pct = (e14n - e50n) / c[-1] * 100
                ema_post_cross = bool(prox_pct < 1.5)
        except:
            pass

        if ema_cross:   ema_signal = 'cross'
        elif ema_trend: ema_signal = 'trend'
        else:           ema_signal = 'none'

        # Volume-confirmed EMA cross with recency decay
        vol_confirmed_cross = False
        cross_score = 0
        try:
            if ema_cross and ema_cross_days_ago and 'Volume' in hist.columns:
                vols = hist['Volume'].fillna(0).values
                i = ema_cross_days_ago
                cross_day_idx = len(vols) - i
                vol_ok = False
                if cross_day_idx > 20:
                    avg20_pre = vols[cross_day_idx - 20:cross_day_idx].mean()
                    cross_vol = vols[cross_day_idx]
                    vol_ok = avg20_pre > 0 and cross_vol >= avg20_pre * 1.5
                if vol_ok:
                    vol_confirmed_cross = True
                    if i <= 2:   cross_score = 18
                    elif i <= 4: cross_score = 14
                    else:        cross_score = 10
                else:
                    cross_score = 8
        except:
            pass

        # EMA pullback — price dipped back to within 2% above EMA14 after being > 2% above it
        # Three conditions:
        # 1. Cross happened >= 2 days ago
        # 2. Price was > 2% above EMA14 at some point in last 5 days (confirmed move away)
        # 3. Price is now 0–2% above EMA14 (dipped back down, still above)
        ema_pullback = False
        try:
            if ema_cross and ema_cross_days_ago and ema_cross_days_ago >= 2 and e14n > 0:
                was_above = any(
                    (float(c[i]) - float(ema14.iloc[i])) / float(ema14.iloc[i]) * 100 > 2.0
                    for i in range(-5, -1)
                )
                pct_now = (float(c[-1]) - e14n) / e14n * 100
                now_near = 0 <= pct_now <= 2.0
                ema_pullback = bool(was_above and now_near)
        except:
            pass

        # Golden cross 30/190
        e30    = float(s.ewm(span=30).mean().iloc[-1])
        golden = False
        if len(c) >= 190:
            e190   = float(s.ewm(span=190).mean().iloc[-1])
            golden = bool(e30 > e190)

        # Real 14-period ADX using Wilder smoothing
        adx = 15.0
        try:
            if 'High' in hist.columns and 'Low' in hist.columns and len(c) >= 28:
                highs = hist['High'].ffill().values
                lows  = hist['Low'].ffill().values
                n = 14
                tr_arr, pdm_arr, ndm_arr = [], [], []
                for i in range(1, len(c)):
                    tr  = max(highs[i]-lows[i], abs(highs[i]-c[i-1]), abs(lows[i]-c[i-1]))
                    up  = highs[i] - highs[i-1]
                    dn  = lows[i-1] - lows[i]
                    pdm_arr.append(up  if up > dn and up > 0 else 0.0)
                    ndm_arr.append(dn  if dn > up and dn > 0 else 0.0)
                    tr_arr.append(tr)
                def wilder(arr, period):
                    out = [None] * period
                    s = sum(arr[:period])
                    out.append(s)
                    for v in arr[period:]:
                        s = s - s/period + v
                        out.append(s)
                    return out
                atr14  = wilder(tr_arr,  n)
                pdm14  = wilder(pdm_arr, n)
                ndm14  = wilder(ndm_arr, n)
                dx_arr = []
                for a, p, nd in zip(atr14, pdm14, ndm14):
                    if a is None or a < 1e-10: continue
                    pdi = 100 * p  / a
                    ndi = 100 * nd / a
                    denom = pdi + ndi
                    dx_arr.append(100 * abs(pdi - ndi) / denom if denom > 1e-10 else 0.0)
                if len(dx_arr) >= n:
                    adx_s = sum(dx_arr[:n])
                    for v in dx_arr[n:]:
                        adx_s = adx_s - adx_s/n + v
                    adx = round(min(60, max(5, adx_s / n)), 1)
        except:
            pass

        # Volume-Price Breakout (VPB)
        vpb_score        = 0
        vpb_detail       = 'none'
        vpb_range_height = 0.0
        avg20_base       = 0.0
        price_coiling    = False
        vol_shrinking    = False
        try:
            if ('High' in hist.columns and 'Low' in hist.columns and
                    'Volume' in hist.columns and len(hist) >= 25):
                vols   = hist['Volume'].fillna(0).values
                closes = c
                highs  = hist['High'].ffill().values
                lows   = hist['Low'].ffill().values

                if len(highs) >= 6:
                    vpb_range_height = float(max(highs[-6:-1]) - min(lows[-6:-1]))

                avg20_base = vols[-25:-5].mean() if len(vols) >= 25 else vols[:-5].mean()

                setup_range_pct = (
                    (max(closes[-6:-1]) - min(closes[-6:-1])) /
                    (min(closes[-6:-1]) + 1e-10) * 100
                ) if len(closes) >= 6 else 999
                price_coiling = setup_range_pct < 4.0

                setup_vols    = vols[-4:-1]
                vol_shrinking = (
                    avg20_base > 0 and
                    all(v < avg20_base * 0.85 for v in setup_vols)
                )

                today_vol   = vols[-1]
                vol_ratio   = today_vol / (avg20_base + 1e-10)
                day_range   = highs[-1] - lows[-1]
                close_pos   = (closes[-1] - lows[-1]) / (day_range + 1e-10)

                if price_coiling and vol_shrinking:
                    if vol_ratio >= 2.0 and close_pos >= 0.7:
                        vpb_score  = 10
                        vpb_detail = 'breakout'
                    elif vol_ratio >= 1.5 and close_pos >= 0.6:
                        vpb_score  = 7
                        vpb_detail = 'breakout'
                    elif vol_ratio >= 1.5 and close_pos < 0.3:
                        vpb_score  = -2
                        vpb_detail = 'distribution'
                    elif vol_ratio < 1.0:
                        vpb_score  = 3
                        vpb_detail = 'coiling'
                    else:
                        vpb_score  = 5
                        vpb_detail = 'weak_breakout'
                elif vol_ratio >= 2.0 and close_pos >= 0.7:
                    vpb_score  = 4
                    vpb_detail = 'vol_only'
                elif price_coiling:
                    vpb_score  = 2
                    vpb_detail = 'coiling'
        except:
            pass

        # Near 38W high (193 trading days — Kite history depth)
        near_38high = False
        try:
            if len(c) >= 50:
                high38 = max(c[-193:]) if len(c)>=193 else max(c)
                near_38high = bool(c[-1] >= high38 * 0.92)
        except:
            pass

        return {
            'rsi':                rsi,
            'macd':               macd,
            'ema_signal':         ema_signal,
            'ema_cross':          ema_cross,
            'ema_cross_days_ago': ema_cross_days_ago,
            'ema_trend':          ema_trend,
            'vol_confirmed_cross':vol_confirmed_cross,
            'cross_score':        cross_score,
            'ema_pre_cross':      ema_pre_cross,
            'ema_post_cross':     ema_post_cross,
            'ema_pullback':       ema_pullback,
            'golden':             golden,
            'adx':                adx,
            'vpb_score':          vpb_score,
            'vpb_detail':         vpb_detail,
            'vpb_range_height':   vpb_range_height,
            'avg20_vol':          float(avg20_base),
            'price_coiling':      price_coiling,
            'vol_shrinking':      vol_shrinking,
            'near_38high':        near_38high,
        }
    except Exception:
        return None


# ════════════════════════════════════════════════════════════════════
# STAGE CLASSIFICATION
# ════════════════════════════════════════════════════════════════════
def classify_stage(tech):
    if not tech:
        return 'none'
    if tech.get('ema_pullback') and tech.get('ema_cross'):
        return 'pullback'
    if tech.get('ema_post_cross'):
        return 'post_cross'
    if tech.get('ema_pre_cross') and tech.get('vpb_detail') in ('breakout', 'weak_breakout'):
        return 'pre_cross'
    if tech.get('ema_cross'):
        return 'post_cross'
    if tech.get('vpb_detail') in ('breakout', 'weak_breakout', 'vol_only'):
        return 'breakout'
    if tech.get('vpb_detail') == 'coiling' or (tech.get('vpb_score', 0) >= 2 and tech.get('vpb_detail') != 'vol_only'):
        return 'coiling'
    if tech.get('ema_trend'):
        return 'trending'
    return 'none'


# ════════════════════════════════════════════════════════════════════
# SCORING
# debtEq=None means unknown (NSE source) — skip D/E tier, score 0 pts
# pe_vs_sector: % premium/discount vs sector median — used when available
# ════════════════════════════════════════════════════════════════════
def _pe_score(pe, pe_vs_sector=None):
    """Returns PE points (max 20). Uses pe_vs_sector when available, else raw PE tiers.
    Scoring curve peaks at 0–+20% premium (slight premium = market confidence):
      0 to +20%   → 20  sweet spot
     -20 to  0%   → 16  slight discount
     -40 to -20%  → 12  meaningful discount
     +20 to +40%  →  8  getting expensive
     > +40%       →  3  priced for perfection
     -60 to -40%  →  3  suspicious discount
     < -60%       →  0  value trap
    """
    if pe_vs_sector is not None:
        if   pe_vs_sector >  40:  return 3
        elif pe_vs_sector >  20:  return 8
        elif pe_vs_sector >=  0:  return 20
        elif pe_vs_sector >= -20: return 16
        elif pe_vs_sector >= -40: return 12
        elif pe_vs_sector >= -60: return 3
        else:                     return 0
    else:
        if   0 < pe < 15: return 20
        elif 0 < pe < 25: return 15
        elif 0 < pe < 35: return 8
        elif 0 < pe < 50: return 3
        else:             return 0


def score(pe, debtEq, roe, dailyVol, tech, pe_vs_sector=None):
    # F score = PE vs sector only, max 20. No base, no D/E (data unreliable).
    # debtEq and roe kept as params for display use only.
    f = _pe_score(pe, pe_vs_sector)

    t = 0
    if tech:
        r = tech['rsi']
        if 45 <= r <= 58:   t += 12
        elif 58 < r <= 65:  t += 7
        elif 40 <= r < 45:  t += 4
        elif 65 < r <= 72:  t += 2

        if tech.get('ema_pre_cross') and tech.get('vpb_detail') in ('breakout', 'weak_breakout'):
            vs = tech.get('vpb_score', 0)
            if vs >= 10:   t += 18
            elif vs >= 7:  t += 14
            else:          t += 12
        elif tech.get('cross_score', 0):
            t += tech.get('cross_score', 0)

        if tech.get('ema_pullback') and tech.get('ema_cross'):
            t += 5

        adx = tech['adx']
        if 20 <= adx <= 35:   t += 10
        elif 15 <= adx < 20:  t += 5
        elif adx > 35:        t += 4

        if not tech.get('ema_pre_cross') and not tech.get('vol_confirmed_cross'):
            t += tech.get('vpb_score', 0)

        if tech.get('macd'):   t += 2

    return min(100, f+t), f, 0, t, 0, 0


# ════════════════════════════════════════════════════════════════════
# TARGET CALCULATION
# ════════════════════════════════════════════════════════════════════
def calc_target(price, mm_target, wk52h, ath, stage=None):
    """Returns (target_price, target_type, upside_pct, upside_rs, mm_conditional).
    target_type: 'MM' | '52W' | 'ATH(9M)'
    mm_conditional: True when MM is selected and stage is 'coiling' (only valid if breakout fires)
    MM is excluded for post_cross, pullback, trending, none stages.
    """
    MM_STAGES = {'breakout', 'pre_cross', 'coiling'}
    include_mm = stage in MM_STAGES if stage else True
    candidates = []
    if include_mm and mm_target and mm_target > price:
        candidates.append(('MM', round(mm_target, 2)))
    if wk52h and wk52h > price:          candidates.append(('52W', round(wk52h, 2)))
    if ath and ath > price:              candidates.append(('ATH(9M)', round(ath, 2)))
    if not candidates:
        return None, None, 0.0, 0.0, False
    target_type, target_price = min(candidates, key=lambda x: x[1])
    upside_pct = round((target_price - price) / price * 100, 1)
    upside_rs  = round(target_price - price, 2)
    mm_conditional = (target_type == 'MM' and stage == 'coiling')
    return target_price, target_type, upside_pct, upside_rs, mm_conditional


# ════════════════════════════════════════════════════════════════════
# SECTOR PE ENRICHMENT
# ════════════════════════════════════════════════════════════════════
def enrich_sector_pe(stocks):
    """Compute sector median PE and PE vs sector for each stock.
    Modifies stocks in-place. Excludes PE <= 0 from median calculation.
    stocks: list of stock dicts, each with 'pe' and 'sector' keys.
    Adds: 'sectorMedianPe', 'peVsSector' (% premium/discount, None if unavailable).
    """
    from collections import defaultdict
    sector_pes = defaultdict(list)
    for s in stocks:
        pe = s.get('pe') or 0
        if pe > 0:
            sector_pes[s.get('sector', 'Others')].append(pe)

    sector_median = {}
    for sector, pes in sector_pes.items():
        sorted_pes = sorted(pes)
        n = len(sorted_pes)
        mid = n // 2
        sector_median[sector] = sorted_pes[mid] if n % 2 else (sorted_pes[mid-1] + sorted_pes[mid]) / 2

    for s in stocks:
        sector = s.get('sector', 'Others')
        median_pe = sector_median.get(sector)
        s['sectorMedianPe'] = round(median_pe, 1) if median_pe else None
        pe = s.get('pe') or 0
        if pe > 0 and median_pe and median_pe > 0:
            s['peVsSector'] = round((pe - median_pe) / median_pe * 100, 1)
        else:
            s['peVsSector'] = None

        # Re-score PE component using sector-relative PE
        pe_vs = s.get('peVsSector')
        old_pe_pts = _pe_score(pe)           # what raw PE scored originally
        new_pe_pts = _pe_score(pe, pe_vs)    # what sector-relative PE scores
        delta = new_pe_pts - old_pe_pts
        if delta != 0:
            s['fScore'] = (s.get('fScore') or 0) + delta
            s['score']  = (s.get('score')  or 0) + delta


# ════════════════════════════════════════════════════════════════════
# SWING SUPPORT / RESISTANCE
# ════════════════════════════════════════════════════════════════════
def calc_swing_sr(highs, lows, price, window=5, cluster_pct=1.5, n=3):
    """
    Identify swing highs and lows, cluster nearby levels, return the
    nearest N resistance levels above price and N support levels below.

    window      : bars on each side required to confirm a swing point
    cluster_pct : merge levels within this % of each other (cluster mean)
    n           : max levels to return on each side
    Returns {'resistance': [{'price':…,'strength':…}, …],
             'support':    [{'price':…,'strength':…}, …]}
    """
    n_bars = len(highs)
    if n_bars < window * 2 + 1:
        return {'resistance': [], 'support': []}

    swing_prices = []
    for i in range(window, n_bars - window):
        h_window = list(highs[i - window:i]) + list(highs[i + 1:i + window + 1])
        l_window = list(lows[i  - window:i]) + list(lows[i  + 1:i + window + 1])
        if float(highs[i]) >= max(float(v) for v in h_window):
            swing_prices.append(float(highs[i]))
        if float(lows[i]) <= min(float(v) for v in l_window):
            swing_prices.append(float(lows[i]))

    if not swing_prices:
        return {'resistance': [], 'support': []}

    swing_prices.sort()

    # Greedy cluster: merge consecutive prices within cluster_pct% of the running mean
    clusters = []
    cur = [swing_prices[0]]
    for p in swing_prices[1:]:
        center = sum(cur) / len(cur)
        if abs(p - center) / center * 100 <= cluster_pct:
            cur.append(p)
        else:
            clusters.append(cur)
            cur = [p]
    clusters.append(cur)

    levels = [(round(sum(c) / len(c), 2), len(c)) for c in clusters]

    resistance = sorted(
        [(lvl, st) for lvl, st in levels if lvl > price * 1.005],
        key=lambda x: x[0]
    )[:n]

    support = sorted(
        [(lvl, st) for lvl, st in levels if lvl < price * 0.995],
        key=lambda x: -x[0]
    )[:n]

    return {
        'resistance': [{'price': r[0], 'strength': r[1]} for r in resistance],
        'support':    [{'price': s[0], 'strength': s[1]} for s in support],
    }


# ════════════════════════════════════════════════════════════════════
# JSON SERIALIZER
# ════════════════════════════════════════════════════════════════════
def _safe_json(data):
    """Serialize to JSON, converting numpy scalars and fixing Infinity/NaN."""
    try:
        import numpy as np
        class _Enc(json.JSONEncoder):
            def default(self, o):
                if isinstance(o, np.bool_):    return bool(o)
                if isinstance(o, np.integer):  return int(o)
                if isinstance(o, np.floating): return float(o)
                return super().default(o)
        return json.dumps(data, cls=_Enc, ensure_ascii=False).replace('Infinity', 'null').replace('NaN', 'null')
    except ImportError:
        return json.dumps(data, ensure_ascii=False).replace('Infinity', 'null').replace('NaN', 'null')
