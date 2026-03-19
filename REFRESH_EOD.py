"""
Dalal Street Scout — EOD Technical Refresh
===========================================
Triggered by Task Scheduler at 3:35 PM Mon-Fri.
Loads cache.json, refreshes RSI/EMA/ADX/score for all stocks, saves back.
Works whether laptop is active, in S0 (Modern Standby), or woken from S4 (Hibernate).
"""

import json, datetime, math, time, os, sys
import warnings
warnings.filterwarnings('ignore')

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
CACHE_FILE = os.path.join(BASE_DIR, 'cache.json')
LOG_FILE   = os.path.join(BASE_DIR, 'market_start.log')

def log(msg):
    ts = datetime.datetime.now().strftime('%d-%m-%Y %H:%M:%S')
    line = f"[{ts}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, 'a') as f:
            f.write(line + '\n')
    except:
        pass

def install(pkg):
    import subprocess
    subprocess.check_call([sys.executable, '-m', 'pip', 'install', pkg, '-q'],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

try:
    import yfinance as yf
except ImportError:
    log("Installing yfinance..."); install('yfinance'); import yfinance as yf

try:
    import pandas as pd
except ImportError:
    log("Installing pandas..."); install('pandas'); import pandas as pd


def calc_technicals(hist):
    if hist is None or len(hist) < 30:
        return None
    try:
        c = hist['Close'].ffill().dropna().values
        s = pd.Series(c)

        # RSI 14
        delta = s.diff()
        gain  = delta.clip(lower=0).rolling(14).mean()
        loss  = (-delta.clip(upper=0)).rolling(14).mean()
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
        if not ema_cross and len(ema14) >= 4:
            for i in range(2, 4):
                if float(ema14.iloc[-i]) > float(ema50.iloc[-i]) and \
                   float(ema14.iloc[-i-1]) <= float(ema50.iloc[-i-1]):
                    ema_cross = True
                    break

        ema14_rising      = e14n > float(ema14.iloc[-5]) if len(ema14) >= 5 else False
        ema14_rising_fast = e14n > float(ema14.iloc[-3]) if len(ema14) >= 3 else False
        ema_trend         = bool(e14n > e50n and ema14_rising and not ema_cross)

        ema_pre_cross = False
        try:
            if not ema_cross and e14n < e50n and c[-1] > 0:
                gap_pct = (e50n - e14n) / c[-1] * 100
                ema_pre_cross = bool(gap_pct < 0.5 and ema14_rising_fast)
        except:
            pass

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

        # Golden cross 30/200
        e30    = float(s.ewm(span=30).mean().iloc[-1])
        golden = False
        if len(c) >= 200:
            e200   = float(s.ewm(span=200).mean().iloc[-1])
            golden = bool(e30 > e200)

        # Real 14-period ADX using Wilder smoothing (+DM/-DM/TR)
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

        # VPB — Volume-Price Breakout (unified signal)
        vpb_score  = 0
        vpb_detail = 'none'
        try:
            if ('High' in hist.columns and 'Low' in hist.columns and
                    'Volume' in hist.columns and len(hist) >= 25):
                vols   = hist['Volume'].fillna(0).values
                closes = c
                highs  = hist['High'].ffill().values
                lows   = hist['Low'].ffill().values

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

                today_vol = vols[-1]
                vol_ratio = today_vol / (avg20_base + 1e-10)
                day_range = highs[-1] - lows[-1]
                close_pos = (closes[-1] - lows[-1]) / (day_range + 1e-10)

                if price_coiling and vol_shrinking:
                    if vol_ratio >= 2.0 and close_pos >= 0.7:
                        vpb_score = 10; vpb_detail = 'breakout'
                    elif vol_ratio >= 1.5 and close_pos >= 0.6:
                        vpb_score = 7;  vpb_detail = 'breakout'
                    elif vol_ratio >= 1.5 and close_pos < 0.3:
                        vpb_score = -2; vpb_detail = 'distribution'
                    elif vol_ratio < 1.0:
                        vpb_score = 3;  vpb_detail = 'coiling'
                    else:
                        vpb_score = 5;  vpb_detail = 'weak_breakout'
                elif vol_ratio >= 2.0 and close_pos >= 0.7:
                    vpb_score = 4; vpb_detail = 'vol_only'
                elif price_coiling:
                    vpb_score = 2; vpb_detail = 'coiling'
        except:
            pass

        # Near 52W high
        near_52high = False
        try:
            if len(c) >= 50:
                high52 = max(c[-252:]) if len(c)>=252 else max(c)
                near_52high = bool(c[-1] >= high52 * 0.92)
        except:
            pass

        return {
            'rsi':          rsi,
            'macd':         macd,
            'ema_signal':   ema_signal,
            'ema_cross':    ema_cross,
            'ema_trend':    ema_trend,
            'golden':       golden,
            'adx':          adx,
            'vpb_score':    vpb_score,
            'vpb_detail':   vpb_detail,
            'near_52high':  near_52high,
        }
    except:
        return None


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
    if tech.get('vpb_detail') in ('breakout', 'weak_breakout'):
        return 'breakout'
    if tech.get('vpb_detail') == 'coiling' or tech.get('vpb_score', 0) >= 2:
        return 'coiling'
    return 'none'


def score(pe, debtEq, roe, dailyVol, tech):
    f = 8
    if 0 < pe < 15:    f += 12
    elif 0 < pe < 25:  f += 9
    elif 0 < pe < 35:  f += 5
    elif 0 < pe < 50:  f += 2

    if debtEq < 0.3:   f += 10
    elif debtEq < 0.7: f += 7
    elif debtEq < 1.0: f += 4
    elif debtEq < 1.5: f += 1

    t = 0
    if tech:
        r = tech['rsi']
        if 45 <= r <= 58:   t += 12
        elif 58 < r <= 65:  t += 7
        elif 40 <= r < 45:  t += 4
        elif 65 < r <= 72:  t += 2

        # EMA cross scored via cross_score if available, else basic
        if tech.get('ema_pre_cross') and tech.get('vpb_detail') in ('breakout', 'weak_breakout'):
            t += 18
        elif tech.get('cross_score', 0):
            t += tech.get('cross_score', 0)
        elif not tech.get('ema_cross') and tech.get('ema_trend'):
            t += 7

        if tech.get('ema_pullback') and tech.get('ema_cross'):
            t += 5

        adx = tech['adx']
        if 20 <= adx <= 35:   t += 10
        elif 15 <= adx < 20:  t += 5
        elif adx > 35:        t += 4

        if not tech.get('ema_pre_cross') and not tech.get('vol_confirmed_cross'):
            t += tech.get('vpb_score', 0)

        if tech.get('macd'):   t += 2

    ct = 0
    if tech and tech.get('near_52high'):
        ct += 1

    l = 0
    if dailyVol >= 5:     l = 10
    elif dailyVol >= 2:   l = 5
    elif dailyVol >= 0.5: l = 2

    return min(100, f+t+ct+l), f, 0, t, ct, l


def main():
    log("EOD refresh started")

    if not os.path.exists(CACHE_FILE):
        log("No cache found — skipping")
        return

    with open(CACHE_FILE, 'r', encoding='utf-8') as f:
        data = json.load(f)

    stocks = data.get('stocks', [])
    if not stocks:
        log("Cache empty — skipping")
        return

    log(f"Refreshing technicals for {len(stocks)} stocks...")
    updated = 0
    failed  = 0

    for s in stocks:
        try:
            hist = yf.Ticker(s['ticker'] + '.NS').history(period='1y', auto_adjust=True)
            if hist is None or len(hist) < 30:
                failed += 1
                continue
            tech = calc_technicals(hist)
            if not tech:
                failed += 1
                continue

            s['rsi']       = tech['rsi']
            s['macd']      = tech['macd']
            s['emaSignal']    = tech['ema_signal']
            s['emaCross']     = tech['ema_cross']
            s['emaPreCross']  = tech['ema_pre_cross']
            s['emaPostCross'] = tech['ema_post_cross']
            s['emaTrend']     = tech['ema_trend']
            s['golden']    = tech['golden']
            s['adx']       = tech['adx']
            s['vpbScore']  = tech['vpb_score']
            s['vpbDetail'] = tech['vpb_detail']
            s['near52High']= tech['near_52high']
            s['stage']     = classify_stage(tech)

            h60 = hist.tail(60)
            s['chartDates']  = [d.strftime('%Y-%m-%d') for d in h60.index]
            s['chartPrices'] = [round(float(p), 2) for p in h60['Close'].values]

            sc, f, c, t, ct, l = score(s.get('pe', 0), s.get('debtEq', 0),
                                       s.get('roe', 0), s.get('dailyVol', 0), tech)
            s['score']   = sc
            s['fScore']  = f
            s['cScore']  = c
            s['tScore']  = t
            s['ctScore'] = ct
            s['lScore']  = l
            updated += 1
            time.sleep(0.15)
        except:
            failed += 1

    now_ist = datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)
    data['last_updated'] = now_ist.strftime('%d %b %Y, %I:%M %p IST').lstrip('0')
    data['saved_at']     = now_ist.isoformat()

    body = json.dumps(data, ensure_ascii=False)
    body = body.replace('Infinity', 'null').replace('NaN', 'null')
    with open(CACHE_FILE, 'w', encoding='utf-8') as f:
        f.write(body)

    log(f"EOD refresh done — {updated} updated, {failed} failed — cache saved")


if __name__ == '__main__':
    main()
