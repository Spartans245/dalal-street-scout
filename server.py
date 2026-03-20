"""
Dalal Street Scout - Local Data Server v6
==========================================
Double-click START_SERVER.bat to run.
Open browser at: http://localhost:5000
"""

import json, datetime, math, time, threading, os, sys
import warnings
warnings.filterwarnings('ignore')

# Force UTF-8 output so Unicode chars in print() don't crash on Windows console
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# Prevent Windows from sleeping while server is running
try:
    import ctypes
    ES_CONTINUOUS      = 0x80000000
    ES_SYSTEM_REQUIRED = 0x00000001
    ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
except Exception:
    pass

from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
from urllib.parse import urlparse

# ── Auto-install ─────────────────────────────────────────────────────
def install(pkg):
    import subprocess
    subprocess.check_call([sys.executable, '-m', 'pip', 'install', pkg, '-q'],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

try:
    import yfinance as yf
except ImportError:
    print("Installing yfinance..."); install('yfinance'); import yfinance as yf

try:
    import pandas as pd
except ImportError:
    print("Installing pandas..."); install('pandas'); import pandas as pd

try:
    import requests
except ImportError:
    print("Installing requests..."); install('requests'); import requests

# ════════════════════════════════════════════════════════════════════
# CONFIG
# ════════════════════════════════════════════════════════════════════
PORT               = 5000
MCAP_MIN_CR        = 1         # include all stocks with valid MCap data
MCAP_MAX_CR        = 9_999_999 # no upper limit — frontend segments by MCap
LIVE_REFRESH       = 5 * 60   # seconds between price refreshes
INFO_WORKERS       = 1        # sequential .info calls — concurrent requests cause yf to return incomplete data (missing marketCap, PE)
INFO_DELAY         = 0.5      # seconds between each .info call (~2700 tickers × 0.5s ≈ 22 min full scan)
HIST_DELAY         = 0.2      # seconds between each history fetch (~2700 tickers × 0.2s ≈ 9 min technicals)
CACHE_MAX_AGE_HRS  = 720   # monthly — technical/fundamental refreshes keep data fresh daily/weekly
TICKER_CACHE_DAYS  = 30       # refresh MCap-filtered ticker list every N days (aligned with monthly full scan)
FUND_REFRESH_DAYS  = 7        # refresh PE/D/E/ROE/sector/name every N days

# ════════════════════════════════════════════════════════════════════
# STATE
# ════════════════════════════════════════════════════════════════════
state = {
    'stocks':           [],
    'last_updated':     None,
    'status':           'starting',
    'market_mode':      'unknown',
    'fetch_progress':   0,
    'fetch_message':    'Starting...',
    'total_scanned':    0,
    'in_range':         0,
    'fund_refreshed_at': None,
    'cache_saved_at':   None,
    'ctrl': {
        'ticker_fetch': {'last_run': None, 'nse_count': 0, 'sme_count': 0},
        'ticker_list':  {'last_run': None, 'total_in_range': 0},
        'price_update': {'last_run': None, 'updated': 0, 'elapsed_sec': 0.0,
                         'batches': 0, 'stocks_received': 0, 'running': False},
        'technicals':   {'last_run': None, 'elapsed_sec': 0.0,
                         'tickers': 0, 'updated': 0, 'running': False},
        'fundamentals': {'last_run': None, 'elapsed_sec': 0.0,
                         'yahoo_calls': 0, 'updated': 0, 'running': False},
        'full_scan':    {'last_run': None, 'elapsed_sec': 0.0,
                         'tickers': 0, 'stocks_ok': 0, 'failed': 0},
    },
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
    if day >= 5:                      return 'weekend'
    if 9*60+15 <= mins < 15*60+30:   return 'open'
    if mins >= 15*60+30:              return 'eod'
    return 'pre'

# ════════════════════════════════════════════════════════════════════
# NSE TICKERS
# ════════════════════════════════════════════════════════════════════
def get_sme_tickers():
    """Fetch NSE Emerge (SME) tickers via NIFTY SME EMERGE index API."""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/json, text/plain, */*',
            'Referer': 'https://www.nseindia.com/',
        }
        session = requests.Session()
        session.get('https://www.nseindia.com', headers=headers, timeout=15)
        r = session.get(
            'https://www.nseindia.com/api/equity-stockIndices?index=NIFTY%20SME%20EMERGE',
            headers=headers, timeout=15
        )
        if r.status_code == 200:
            data = r.json()
            tickers = [x['symbol'] for x in data.get('data', []) if x.get('symbol')]
            print(f"  ✅ Got {len(tickers)} SME/Emerge tickers from NSE")
            return tickers
    except Exception as e:
        print(f"  ⚠ SME ticker fetch failed: {e}")
    return []

def get_nse_tickers():
    try:
        headers = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://www.nseindia.com/'}
        url  = 'https://archives.nseindia.com/content/equities/EQUITY_L.csv'
        resp = requests.get(url, headers=headers, timeout=20)
        if resp.status_code == 200:
            from io import StringIO
            df = pd.read_csv(StringIO(resp.text))
            if 'SYMBOL' in df.columns:
                main_tickers = df['SYMBOL'].dropna().str.strip().tolist()
                print(f"  ✅ Got {len(main_tickers)} main board tickers from NSE")
                sme_tickers  = get_sme_tickers()
                combined     = list(dict.fromkeys(main_tickers + sme_tickers))  # dedup, preserve order
                print(f"  ✅ Total: {len(combined)} tickers ({len(main_tickers)} main + {len(sme_tickers)} SME)")
                with state_lock:
                    state['ctrl']['ticker_fetch']['last_run']  = get_ist().isoformat()
                    state['ctrl']['ticker_fetch']['nse_count'] = len(main_tickers)
                    state['ctrl']['ticker_fetch']['sme_count'] = len(sme_tickers)
                return combined
    except Exception as e:
        print(f"  ⚠ NSE download failed: {e}")

    print("  ⚠ Using fallback ticker list")
    return [
        'RVNL','IRFC','IRCON','NBCC','NCC','RITES','RAILTEL','HGINFRA','KEC',
        'PNCINFRA','KNRCON','SJVN','NHPC','IRCTC','GPPL','ENGINERSIN','DILIPBLDNG',
        'DATAPATTNS','MTAR','SOLARINDS','GRSE','COCHINSHIP','MIDHANI','BEL','BEML',
        'APOLLOMICRO','ZEN','PARAS','DYNAMATECH','HAL','DEEPAKNTR','AARTIIND',
        'NAVINFLUOR','ALKYLAMINE','FINEORG','CLEAN','VINATIORG','SUDARSCHEM',
        'NOCIL','IGPL','PCBL','ROSSARI','TATACHEM','ATUL','CAMLIN','CHEMPLAST',
        'GNFC','GUJALKALI','MEGHMANI','NEOGEN','ALKEM','AJANTPHARM','IPCA',
        'NATCOPHARM','GRANULES','LAURUSLABS','SOLARA','GLENMARK','MARKSANS',
        'ERIS','CAPLIPOINT','WINDLAS','SYNGENE','THYROCARE','KRSNAA','VIJAYADIAG',
        'RAINBOW','NEULANDLAB','JBCHEPHARM','AARTIDRUGS','BLISSGVS','SEQUENT',
        'STRIDES','WOCKHARDT','MPHASIS','KPITTECH','TANLA','TATAELXSI',
        'HAPPSTMNDS','MASTEK','RATEGAIN','NEWGEN','INTELLECT','ZENSAR','CYIENT',
        'BIRLASOFT','ROUTE','NUCLEUS','TBOTEK','AFFLE','ONMOBILE','SAKSOFT',
        'NIIT','CMSINFO','ECLERX','LATENTVIEW','QUICKHEAL','NELCO','CDSL','MCX',
        'ANGELONE','MOTILALOFS','IIFL','CHOLAFIN','MANAPPURAM','MUTHOOTMICRO',
        'APTUS','HOMEFIRST','SPANDANA','CREDITACC','FUSION','SUNDARMFIN','REPCO',
        'SATIN','SURYODAY','UJJIVANSFB','UJJIVAN','SUPRAJIT','ENDURANCE',
        'CRAFTSMAN','GABRIEL','SUBROS','SUNDRMFAST','MINDA','OLECTRA','EXIDE',
        'LUMAXTECH','FIEM','IGARASHI','TIINDIA','VSTIND','ZYDUSWELL','JYOTHYLAB',
        'BAJAJCON','BIKAJI','DEVYANI','SAPPHIRE','WESTLIFE','BARBEQUE','EASEMYTRIP',
        'NYKAA','DELHIVERY','CAMPUS','CELLO','DBCORP','EMAMILTD','RADICO',
        'TASTYBITSS','VENKY','WONDERLA','CARTRADE','ZAGGLE','PAGEIND','GOKALDAS',
        'RAYMOND','VEDANT','KITEX','VARDHMAN','TRIDENT','WELSPUN','SPORTKING',
        'FILATEX','DOLLAR','RUPA','SUTLEJTEX','ARVIND','CANTABIL','DONEAR',
        'GARWARE','HIMATSEIDE','SOBHA','MAHLIFE','KOLTEPATIL','SUNTECK',
        'GREENPANEL','CENTURYPLY','ASTRAL','SUPREMEIND','ORIENTBELL','CERA',
        'SOMANYCER','KAJARIACER','ACRYSIL','HEIDELBERG','NUVOCO','MOIL','NMDC',
        'NATIONALUM','HINDCOPPER','JINDALSAW','RATNAMANI','WELCORP','SHYAMMETL',
        'TINPLATE','BANDHANBNK','IDFCFIRSTB','FEDERALBNK','KARURVYSYA','DCBBANK',
        'EQUITASBNK','ESAFSFB','UTKARSHBNK','AUBANK','RBLBANK','CSBBANK',
        'COROMANDEL','GSFC','CHAMBLFERT','KSCL','RALLIS','UPL','BAYER',
        'KAVERI','GODREJAGRO','INSECTICID','GATI','TCI','ALLCARGO','SNOWMAN',
        'ZEEL','SUNTV','TVTODAY','JAGRAN','SAREGAMA','TIPS','BALAJI',
        'INDHOTEL','TAJGVK','CHALET','LEMONTRE','RECLTD','PFC','IREDA',
    ]

# ════════════════════════════════════════════════════════════════════
# TECHNICALS — breakout-focused
# ════════════════════════════════════════════════════════════════════
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
        ema_cross_days_ago = None  # how many days ago the cross happened
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
        # Combined with VPB breakout → cross happening within 1 day
        ema_pre_cross = False
        try:
            if not ema_cross and e14n < e50n and c[-1] > 0:
                gap_pct = (e50n - e14n) / c[-1] * 100
                ema_pre_cross = bool(gap_pct < 0.5 and ema14_rising_fast)
        except:
            pass

        # Post-cross: just crossed (1-2 days ago) and lines still in close proximity
        # Gap < 1.5% means stock hasn't surged away — still in the entry window
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
        # cross_score: 18 (fresh+vol), 14 (3-4d+vol), 10 (5d+vol), 8 (cross no vol), 0
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
                    if i <= 2:   cross_score = 18  # 1-2 days ago: fresh signal
                    elif i <= 4: cross_score = 14  # 3-4 days ago: still valid
                    else:        cross_score = 10  # 5 days ago: aging
                else:
                    cross_score = 8  # cross without volume confirmation
        except:
            pass

        # EMA pullback setup — price pulled back within 2% of 14 EMA after a cross
        # (the "kiss-back" — high probability re-entry after initial surge)
        ema_pullback = False
        try:
            if ema_cross and ema_cross_days_ago and ema_cross_days_ago >= 2 and e14n > 0:
                pct_from_ema = abs(float(c[-1]) - e14n) / e14n * 100
                ema_pullback = bool(pct_from_ema <= 2.0)
        except:
            pass

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
                # Wilder smoothing: seed with sum of first n, then rolling
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

        # Volume-Price Breakout (VPB) — unified signal replacing vol_contract/vol_expand/consolidating
        # Looks for: price coiling (tight range) + shrinking volume (setup)
        # then a trigger candle: big volume + close near top of range (breakout)
        # Penalises: high volume + close near low (distribution)
        vpb_score        = 0
        vpb_detail       = 'none'   # coiling | breakout | weak_breakout | distribution | vol_only
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

                # 5-day high/low range for measured move target
                if len(highs) >= 6:
                    vpb_range_height = float(max(highs[-6:-1]) - min(lows[-6:-1]))

                # Baseline: 20d avg excluding last 5 days (clean pre-setup reference)
                avg20_base = vols[-25:-5].mean() if len(vols) >= 25 else vols[:-5].mean()

                # --- Setup: last 5 days (excluding today) ---
                setup_range_pct = (
                    (max(closes[-6:-1]) - min(closes[-6:-1])) /
                    (min(closes[-6:-1]) + 1e-10) * 100
                ) if len(closes) >= 6 else 999
                price_coiling = setup_range_pct < 4.0   # tight price range

                setup_vols    = vols[-4:-1]              # last 3 days before today
                vol_shrinking = (
                    avg20_base > 0 and
                    all(v < avg20_base * 0.85 for v in setup_vols)
                )

                # --- Trigger: today's candle ---
                today_vol   = vols[-1]
                vol_ratio   = today_vol / (avg20_base + 1e-10)
                day_range   = highs[-1] - lows[-1]
                close_pos   = (closes[-1] - lows[-1]) / (day_range + 1e-10)  # 0=low, 1=high

                # Scoring hierarchy
                if price_coiling and vol_shrinking:
                    if vol_ratio >= 2.0 and close_pos >= 0.7:
                        vpb_score  = 10   # perfect: setup + strong breakout candle
                        vpb_detail = 'breakout'
                    elif vol_ratio >= 1.5 and close_pos >= 0.6:
                        vpb_score  = 7    # good breakout but slightly weaker
                        vpb_detail = 'breakout'
                    elif vol_ratio >= 1.5 and close_pos < 0.3:
                        vpb_score  = -2   # distribution — sellers dumping into volume
                        vpb_detail = 'distribution'
                    elif vol_ratio < 1.0:
                        vpb_score  = 3    # coiling with no trigger yet — watch
                        vpb_detail = 'coiling'
                    else:
                        vpb_score  = 5    # breakout candle but close not convincing
                        vpb_detail = 'weak_breakout'
                elif vol_ratio >= 2.0 and close_pos >= 0.7:
                    vpb_score  = 4        # volume breakout but no coiling setup
                    vpb_detail = 'vol_only'
                elif price_coiling:
                    vpb_score  = 2        # price coiling but volume not shrinking
                    vpb_detail = 'coiling'
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
            'near_52high':        near_52high,
        }
    except Exception as e:
        return None

# ════════════════════════════════════════════════════════════════════
# STAGE CLASSIFICATION
# Lifecycle: coiling → breakout → pre_cross → post_cross → pullback
# Priority highest to lowest so a stock lands in the most advanced stage.
# ════════════════════════════════════════════════════════════════════
def classify_stage(tech):
    if not tech:
        return 'none'
    # Pullback: cross happened, price kissed back to 14 EMA (re-entry)
    if tech.get('ema_pullback') and tech.get('ema_cross'):
        return 'pullback'
    # Post-cross: 14 EMA just crossed 50 EMA (1-2d ago), lines still proximate
    if tech.get('ema_post_cross'):
        return 'post_cross'
    # Pre-cross: 14 EMA < 50 EMA but gap < 0.5% and closing fast + VPB fired
    if tech.get('ema_pre_cross') and tech.get('vpb_detail') in ('breakout', 'weak_breakout'):
        return 'pre_cross'
    # Older cross (3-5d, lines diverged) — still valid, lower conviction
    if tech.get('ema_cross'):
        return 'post_cross'
    # Breakout: VPB trigger fired (including vol_only burst), no cross proximity yet
    if tech.get('vpb_detail') in ('breakout', 'weak_breakout', 'vol_only'):
        return 'breakout'
    # Coiling: setup in place, waiting for trigger (exclude vol_only — no setup)
    if tech.get('vpb_detail') == 'coiling' or (tech.get('vpb_score', 0) >= 2 and tech.get('vpb_detail') != 'vol_only'):
        return 'coiling'
    # Trending: 14 EMA above 50 EMA, uptrend established — no fresh signal yet
    if tech.get('ema_trend'):
        return 'trending'
    return 'none'


# ════════════════════════════════════════════════════════════════════
# SCORING
# ════════════════════════════════════════════════════════════════════
def score(pe, debtEq, roe, dailyVol, tech):
    # Fundamentals (30 pts) — ROE not scored, warning badge only
    f = 8   # no pledge assumed — verify on screener.in

    if 0 < pe < 15:    f += 12
    elif 0 < pe < 25:  f += 9
    elif 0 < pe < 35:  f += 5
    elif 0 < pe < 50:  f += 2

    if debtEq < 0.3:   f += 10
    elif debtEq < 0.7: f += 7
    elif debtEq < 1.0: f += 4
    elif debtEq < 1.5: f += 1

    # Technicals (40 pts) — breakout timing
    t = 0
    if tech:
        r = tech['rsi']
        if 45 <= r <= 58:   t += 12
        elif 58 < r <= 65:  t += 7
        elif 40 <= r < 45:  t += 4
        elif 65 < r <= 72:  t += 2

        # Pre-cross: VPB fired + 14 EMA within 0.5% of 50 EMA — tiered by VPB quality
        # +18: perfect breakout (vpb_score=10), +14: good breakout (vpb_score=7), +12: weak breakout (vpb_score=5)
        if tech.get('ema_pre_cross') and tech.get('vpb_detail') in ('breakout', 'weak_breakout'):
            vs = tech.get('vpb_score', 0)
            if vs >= 10:   t += 18
            elif vs >= 7:  t += 14
            else:          t += 12
        # Post-cross / regular cross: tiered by recency + volume
        elif tech.get('cross_score', 0):
            t += tech.get('cross_score', 0)
        # ema_trend alone = TRENDING tab qualifier only, not scored
        # (stock already moved, no fresh entry signal)

        # Pullback to EMA after a cross = high-probability re-entry bonus
        if tech.get('ema_pullback') and tech.get('ema_cross'):
            t += 5

        adx = tech['adx']
        if 20 <= adx <= 35:   t += 10
        elif 15 <= adx < 20:  t += 5
        elif adx > 35:        t += 4

        # VPB score — suppressed if pre_cross or vol_confirmed_cross already captured it
        if not tech.get('ema_pre_cross') and not tech.get('vol_confirmed_cross'):
            t += tech.get('vpb_score', 0)

        if tech.get('macd'):   t += 2

    l = 0  # liquidity is a UI filter only, not scored
    c = 0  # catalyst — manual only for now
    ct = 0  # context removed from scoring — near52High kept as a badge only
    return min(100, f+t+c), f, c, t, ct, l

# ════════════════════════════════════════════════════════════════════
# TARGET CALCULATION
# ════════════════════════════════════════════════════════════════════
def calc_target(price, mm_target, wk52h, ath):
    """Nearest overhead target from MM measured move, 52W High, and ATH (5Y).
    Returns (target_price, target_type, upside_pct, upside_rs).
    target_type: 'MM' | '52W' | 'ATH'
    """
    candidates = []
    if mm_target and mm_target > price:  candidates.append(('MM',  round(mm_target, 2)))
    if wk52h and wk52h > price:          candidates.append(('52W', round(wk52h, 2)))
    if ath and ath > price:              candidates.append(('ATH', round(ath, 2)))
    if not candidates:
        return None, None, 0.0, 0.0
    target_type, target_price = min(candidates, key=lambda x: x[1])
    upside_pct = round((target_price - price) / price * 100, 1)
    upside_rs  = round(target_price - price, 2)
    return target_price, target_type, upside_pct, upside_rs

# ════════════════════════════════════════════════════════════════════
# FETCH ALL STOCKS
# ════════════════════════════════════════════════════════════════════
TICKER_CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tickers_cache.json')


def load_ticker_cache():
    """Load tickers_cache.json if < TICKER_CACHE_DAYS old. Returns list of {ticker,mcap} or None."""
    try:
        if not os.path.exists(TICKER_CACHE_FILE):
            return None
        with open(TICKER_CACHE_FILE) as f:
            data = json.load(f)
        saved_at = datetime.datetime.fromisoformat(data['saved_at'])
        age_days = (get_ist() - saved_at).days
        if age_days > TICKER_CACHE_DAYS:
            print(f"  ⚠ Ticker cache is {age_days}d old (>{TICKER_CACHE_DAYS}d) — rebuilding")
            return None
        if not data['tickers']:
            print(f"  ⚠ Ticker cache is empty — rebuilding")
            return None
        print(f"  ✅ Ticker cache: {len(data['tickers'])} stocks in MCap range (age: {age_days}d)")
        with state_lock:
            state['ctrl']['ticker_list']['last_run']       = data['saved_at']
            state['ctrl']['ticker_list']['total_in_range'] = len(data['tickers'])
        return data['tickers']
    except Exception as e:
        print(f"  ⚠ Ticker cache load error: {e}")
        return None

def _scan_one(ticker, prefiltered_mcap=None, cached_fund=None, cached_hist=None):
    """Fetch and process a single ticker. Returns result dict or None.
    If prefiltered_mcap is provided and non-zero (from ticker cache), MCap check is skipped.
    If cached_fund is provided (fundamentals fresh), .info call is skipped entirely.
    If cached_hist is provided (pre-downloaded batch history), .history() call is skipped."""
    ns = ticker.strip().replace(' ', '') + '.NS'
    try:
        if cached_fund is not None and cached_hist is not None:
            # --- Fast path: all data provided (used by refresh_technicals) ---
            hist = cached_hist
            pe     = cached_fund['pe']
            debtEq = cached_fund['debtEq']
            roe    = cached_fund['roe']
            sector = cached_fund['sector']
            name   = cached_fund['name']
            mcap_cr = prefiltered_mcap if prefiltered_mcap else cached_fund.get('mcap', 0)

            closes  = hist['Close'].ffill()
            price   = round(float(closes.iloc[-1]), 2)
            prev    = float(closes.iloc[-2]) if len(closes) > 1 else price
            change  = round((price - prev) / prev * 100, 2) if prev else 0.0

            highs = hist['High'].ffill().values
            lows  = hist['Low'].ffill().values
            wk52h = round(float(max(highs[-252:])) if len(highs) >= 252 else float(max(highs)), 2)
            wk52l = round(float(min(lows[-252:]))  if len(lows)  >= 252 else float(min(lows)),  2)

            avg_vol = float(hist['Volume'].tail(20).mean())
            dvol    = round(avg_vol * price / 1e7, 1)
        else:
            # --- Full path: fetch history + .info from one Ticker object ---
            time.sleep(INFO_DELAY)
            t    = yf.Ticker(ns)
            hist = t.history(period='5y', auto_adjust=True)
            if hist is None or len(hist) < 30:
                return None
            info = t.info

            # MCap check — skip if prefiltered with a real value (>0) from ticker cache
            if prefiltered_mcap:
                mcap_cr = prefiltered_mcap
            else:
                mcap_raw = info.get('marketCap', 0) or 0
                if not mcap_raw:
                    # Fallback: sharesOutstanding × last close price
                    shares = info.get('sharesOutstanding', 0) or 0
                    if shares:
                        price_approx = float(hist['Close'].iloc[-1])
                        mcap_raw = shares * price_approx
                mcap_cr = round(mcap_raw / 1e7, 0)
                # Only hard-exclude if MCap is known AND out of range
                if mcap_cr > 0 and (mcap_cr < MCAP_MIN_CR or mcap_cr > MCAP_MAX_CR):
                    return None

            price = float(
                info.get('currentPrice') or
                info.get('regularMarketPrice') or
                float(hist['Close'].iloc[-1])
            )
            if not price or math.isnan(price) or price <= 0:
                return None

            prev   = info.get('previousClose') or (float(hist['Close'].iloc[-2]) if len(hist) > 1 else price)
            change = round((price - float(prev)) / float(prev) * 100, 2) if prev else 0.0

            pe = float(info.get('trailingPE') or info.get('forwardPE') or 0)
            if math.isnan(pe): pe = 0.0
            pe = round(pe, 1)

            roe_r = float(info.get('returnOnEquity') or 0)
            if math.isnan(roe_r): roe_r = 0.0
            roe = round(roe_r * 100, 1)

            deq_r = float(info.get('debtToEquity') or 0)
            if math.isnan(deq_r): deq_r = 0.0
            debtEq = round(deq_r / 100, 2)

            avg_vol = info.get('averageVolume', 0) or 0
            dvol    = round(avg_vol * price / 1e7, 1)

            wk52h  = float(info.get('fiftyTwoWeekHigh') or 0)
            wk52l  = float(info.get('fiftyTwoWeekLow')  or 0)

            sector = info.get('sector') or 'Others'
            name   = info.get('longName') or info.get('shortName') or ticker

        if not price or math.isnan(price) or price <= 0:
            return None

        pct52h = round((price - wk52h) / wk52h * 100, 1) if wk52h else 0
        pct52l = round((price - wk52l) / wk52l * 100, 1) if wk52l else 0
        roe_warn = 'high' if roe > 20 else 'medium' if roe > 12 else 'low' if roe > 0 else 'na'

        tech = calc_technicals(hist)

        # ATH from 5-year history (already fetched — no extra API call)
        ath = 0.0
        try:
            if 'High' in hist.columns and len(hist) > 0:
                ath_raw = float(hist['High'].max())
                if not math.isnan(ath_raw): ath = ath_raw
        except: pass

        vpb_rh    = tech['vpb_range_height'] if tech else 0.0
        mm_target = round(price + vpb_rh, 2) if vpb_rh > 0 else None
        target_price, target_type, upside_pct, upside_rs = calc_target(price, mm_target, wk52h, ath)

        sc, f, c, t2, ct2, l = score(pe, debtEq, roe, dvol, tech)

        chart_prices, chart_dates = [], []
        try:
            h60    = hist.tail(60)
            closes = h60['Close'].ffill().tolist()
            chart_prices = [round(float(x), 2) for x in closes if not math.isnan(float(x))]
            chart_dates  = [str(d.date()) for d in h60.index.tolist()]
        except: pass

        return {
            'ticker':          ticker,
            'name':            name,
            'sector':          sector,
            'price':           round(price, 2),
            'change':          change,
            'pe':              pe,
            'mcap':            int(mcap_cr),
            'promoterHolding': 0,
            'pledging':        0,
            'debtEq':          debtEq,
            'roe':             roe,
            'roeWarn':         roe_warn,
            'wk52High':        round(wk52h, 2),
            'wk52Low':         round(wk52l, 2),
            'pctFrom52High':   pct52h,
            'pctFrom52Low':    pct52l,
            'rsi':             tech['rsi']                if tech else 50.0,
            'adx':             tech['adx']                if tech else 15.0,
            'macd':            tech['macd']               if tech else False,
            'emaSignal':       tech['ema_signal']         if tech else 'none',
            'emaCross':        tech['ema_cross']          if tech else False,
            'emaCrossDays':    tech['ema_cross_days_ago'] if tech else None,
            'emaTrend':        tech['ema_trend']          if tech else False,
            'volConfirm':      tech['vol_confirmed_cross'] if tech else False,
            'crossScore':      tech['cross_score']        if tech else 0,
            'emaPreCross':     tech['ema_pre_cross']      if tech else False,
            'emaPostCross':    tech['ema_post_cross']     if tech else False,
            'emaPullback':     tech['ema_pullback']       if tech else False,
            'golden':          tech['golden']             if tech else False,
            'vpbScore':        tech['vpb_score']          if tech else 0,
            'vpbDetail':       tech['vpb_detail']         if tech else 'none',
            'vpbRangeHeight':  tech['vpb_range_height']   if tech else 0.0,
            'avg20Vol':        tech['avg20_vol']           if tech else 0.0,
            'priceCoiling':    tech['price_coiling']       if tech else False,
            'volShrinking':    tech['vol_shrinking']       if tech else False,
            'near52High':      tech['near_52high']        if tech else False,
            'stage':           classify_stage(tech),
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
            'ath':             round(ath, 2),
            'mmTarget':        mm_target,
            'targetPrice':     target_price,
            'targetType':      target_type,
            'upsidePct':       upside_pct,
            'upsideRs':        upside_rs,
        }
    except Exception:
        return None


def fetch_all_stocks():
    import threading
    from concurrent.futures import ThreadPoolExecutor, as_completed

    _t0 = time.time()
    with state_lock:
        state['status']         = 'fetching'
        state['fetch_progress'] = 0
        state['fetch_message']  = 'Loading ticker list...'
        state['in_range']       = 0

    # Always fetch fresh ticker list from NSE — this is a full scan
    # tickers_cache.json used only as fallback if NSE is unreachable
    raw = get_nse_tickers()
    if raw:
        ticker_items = [{'ticker': t, 'mcap': 0} for t in raw]
        print(f"  ✅ NSE ticker list fetched: {len(ticker_items)} tickers")
    else:
        ticker_items = load_ticker_cache() or []
        print(f"  ⚠ NSE unreachable — falling back to cached list ({len(ticker_items)} tickers)")

    total = len(ticker_items)

    with state_lock:
        state['total_scanned'] = total
        state['fetch_message'] = f'Scanning {total} in-range stocks (sequential .info)...'

    print(f"\n{'='*60}")
    print(f"  Scanning {total} pre-filtered stocks  ₹{MCAP_MIN_CR}–{MCAP_MAX_CR} Cr MCap")
    print(f"  Mode: sequential — history + .info per ticker ({INFO_DELAY}s delay)")
    print(f"{'='*60}\n")

    # Full rescan: sequential per-ticker fetch — history + info from same Ticker object,
    # no batch downloads (yf.download silently drops NSE tickers, reducing coverage)
    print(f"  🔄 Full rescan — sequential history + .info per ticker ({INFO_DELAY}s delay)")

    with state_lock:
        state['fetch_message'] = f'Scanning {total} stocks sequentially ({INFO_DELAY}s/ticker)...'

    results      = []
    results_lock = threading.Lock()
    counter      = [0, 0]  # [scanned, failed]

    def worker(item):
        result = _scan_one(item['ticker'], prefiltered_mcap=item['mcap'])
        with results_lock:
            counter[0] += 1
            if result is None:
                counter[1] += 1
            else:
                results.append(result)
                star = '⭐' if result['score'] >= 65 else '  '
                print(f"  ✅ {result['ticker']:<16} ₹{result['price']:>9,.2f}  Score:{result['score']:>3}  {star}")
            n = counter[0]
            if n % 50 == 0:
                pct = int(n / total * 100)
                with state_lock:
                    state['fetch_progress'] = pct
                    state['fetch_message']  = f'Scanning {n} of {total}  ({len(results)} found)'
                    state['in_range']       = len(results)

    with ThreadPoolExecutor(max_workers=INFO_WORKERS) as ex:
        list(ex.map(worker, ticker_items))

    ist    = get_ist()
    strong = [s for s in results if s['score'] >= 65]

    # Safety: if scan returned suspiciously few results (likely rate-limited),
    # keep the old state and do NOT overwrite cache
    fail_rate = counter[1] / total if total else 0
    if len(results) == 0 or fail_rate > 0.90:
        print(f"\n⚠ SCAN ABORTED — {counter[1]}/{total} failures ({fail_rate:.0%}) — likely rate-limited.")
        print(f"  Cache NOT overwritten. Keeping previous data.")
        with state_lock:
            state['fetch_progress'] = 100
            state['fetch_message']  = f'⚠ Rate-limited — {counter[1]}/{total} failed. Previous data kept.'
            state['status']         = 'eod'
        return

    with state_lock:
        state['stocks']           = results
        state['last_updated']     = ist.strftime('%d %b %Y, %I:%M %p IST')
        state['market_mode']      = get_market_mode()
        state['status']           = 'live' if get_market_mode() == 'open' else 'eod'
        state['fetch_progress']   = 100
        state['fetch_message']    = f'Done — {len(results)} stocks in range'
        state['in_range']         = len(results)
        state['total_scanned']    = total
        state['fund_refreshed_at'] = ist.isoformat()
        state['ctrl']['full_scan'].update({
            'last_run':    ist.isoformat(),
            'elapsed_sec': round(time.time() - _t0, 1),
            'tickers':     total,
            'stocks_ok':   len(results),
            'failed':      counter[1],
        })

    print(f"\n{'='*60}")
    print(f"  ✅ {len(results)} stocks in ₹{MCAP_MIN_CR}–{MCAP_MAX_CR} Cr range")
    print(f"  ⭐ {len(strong)} strong entry candidates (score 65+)")
    print(f"  ❌ {counter[1]} tickers failed / not in range")
    print(f"  📡 Browser: http://localhost:{PORT}")
    print(f"{'='*60}\n")

    # Save ticker cache from verified results — these already passed MCap filter correctly
    try:
        ticker_data = [{'ticker': s['ticker'], 'mcap': s['mcap']} for s in results]
        cache_data  = {'saved_at': get_ist().isoformat(), 'tickers': ticker_data}
        with open(TICKER_CACHE_FILE, 'w') as f:
            f.write(_safe_json(cache_data))
        with state_lock:
            state['ctrl']['ticker_list']['last_run']       = get_ist().isoformat()
            state['ctrl']['ticker_list']['total_in_range'] = len(ticker_data)
        print(f"  💾 Ticker cache saved: {len(ticker_data)} stocks → tickers_cache.json")
    except Exception as e:
        print(f"  ⚠ Ticker cache save failed: {e}")

# ════════════════════════════════════════════════════════════════════
# PRICE REFRESH (market hours — fast, no history re-fetch)
# ════════════════════════════════════════════════════════════════════
def refresh_prices():
    with state_lock:
        if state.get('status') == 'fetching':
            print("  ⏭️  Skipping price refresh — full scan in progress")
            return
        stocks = list(state['stocks'])
    if not stocks:
        return

    _t0 = time.time()
    with state_lock:
        state['ctrl']['price_update']['running'] = True

    BATCH_SIZE  = 50
    MAX_WORKERS = 3

    tickers_ns = [s['ticker'] + '.NS' for s in stocks]
    batches    = [tickers_ns[i:i+BATCH_SIZE] for i in range(0, len(tickers_ns), BATCH_SIZE)]
    print(f"  🔄 Refreshing {len(stocks)} prices ({len(batches)} batches × {MAX_WORKERS} workers)...")

    # Shared dict: ticker_ns -> (price, prev_close, today_high, today_low, today_vol)
    price_map = {}
    import threading
    map_lock  = threading.Lock()

    def fetch_batch(batch):
        for attempt in range(2):
            try:
                data = yf.download(batch, period='5d', interval='1d',
                                   auto_adjust=True, progress=False, threads=False)
                if data.empty:
                    return
                # yfinance returns MultiIndex columns when batch > 1, Series when batch == 1
                multi = len(batch) > 1
                def _col(name, ticker):
                    try:
                        s = data[name][ticker] if multi else data[name]
                        return s.dropna()
                    except:
                        return None
                for ticker in batch:
                    try:
                        c_vals = _col('Close',  ticker)
                        if c_vals is None or len(c_vals) < 2:
                            continue
                        h_vals = _col('High',   ticker)
                        l_vals = _col('Low',    ticker)
                        v_vals = _col('Volume', ticker)
                        with map_lock:
                            price_map[ticker] = (
                                float(c_vals.iloc[-1]),
                                float(c_vals.iloc[-2]),
                                float(h_vals.iloc[-1]) if h_vals is not None and len(h_vals) > 0 else 0.0,
                                float(l_vals.iloc[-1]) if l_vals is not None and len(l_vals) > 0 else 0.0,
                                float(v_vals.iloc[-1]) if v_vals is not None and len(v_vals) > 0 else 0.0,
                            )
                    except:
                        pass
                return
            except Exception as e:
                if attempt == 0:
                    print(f"  ⚠ Batch failed (retrying): {e}")
                else:
                    print(f"  ✗ Batch failed after retry: {e}")

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        list(ex.map(fetch_batch, batches))

    updated = 0
    for s in stocks:
        ns = s['ticker'] + '.NS'
        if ns not in price_map:
            continue
        try:
            price, prev, today_high, today_low, today_vol = price_map[ns]
            if math.isnan(price):
                continue
            prev   = prev or s['price']
            change = round((price - prev) / prev * 100, 2) if prev else s['change']
            s['price']  = round(price, 2)
            s['change'] = change
            # Recalculate price-derived fields
            wk52h = s.get('wk52High') or 0
            wk52l = s.get('wk52Low') or 0
            if wk52h:
                s['pctFrom52High'] = round((price - wk52h) / wk52h * 100, 1)
                s['near52High']    = price >= wk52h * 0.92
            if wk52l:
                s['pctFrom52Low']  = round((price - wk52l) / wk52l * 100, 1)
            # VPB intraday recalculation — recompute trigger using live vol/candle
            avg20 = s.get('avg20Vol', 0.0)
            if avg20 > 0 and today_high > today_low > 0:
                vol_ratio = today_vol / (avg20 + 1e-10)
                close_pos = (price - today_low) / (today_high - today_low + 1e-10)
                pc = s.get('priceCoiling', False)
                vs = s.get('volShrinking', False)
                vpb_score, vpb_detail = 0, 'none'
                if pc and vs:
                    if vol_ratio >= 2.0 and close_pos >= 0.7:
                        vpb_score, vpb_detail = 10, 'breakout'
                    elif vol_ratio >= 1.5 and close_pos >= 0.6:
                        vpb_score, vpb_detail = 7, 'breakout'
                    elif vol_ratio >= 1.5 and close_pos < 0.3:
                        vpb_score, vpb_detail = -2, 'distribution'
                    elif vol_ratio < 1.0:
                        vpb_score, vpb_detail = 3, 'coiling'
                    else:
                        vpb_score, vpb_detail = 5, 'weak_breakout'
                elif vol_ratio >= 2.0 and close_pos >= 0.7:
                    vpb_score, vpb_detail = 4, 'vol_only'
                elif pc:
                    vpb_score, vpb_detail = 2, 'coiling'
                s['vpbScore']  = vpb_score
                s['vpbDetail'] = vpb_detail
                # Reclassify stage with updated VPB
                stage_tech = {
                    'ema_cross':           s.get('emaCross', False),
                    'ema_pre_cross':       s.get('emaPreCross', False),
                    'ema_post_cross':      s.get('emaPostCross', False),
                    'ema_pullback':        s.get('emaPullback', False),
                    'ema_trend':           s.get('emaTrend', False),
                    'vol_confirmed_cross': s.get('volConfirm', False),
                    'vpb_detail':          vpb_detail,
                    'vpb_score':           vpb_score,
                }
                s['stage'] = classify_stage(stage_tech)
            # Recalculate MM target and upside from live price
            vpb_rh    = s.get('vpbRangeHeight', 0.0)
            mm_target = round(price + vpb_rh, 2) if vpb_rh > 0 else None
            s['mmTarget'] = mm_target
            t_price, t_type, upside_pct, upside_rs = calc_target(
                price, mm_target, s.get('wk52High', 0), s.get('ath', 0)
            )
            s['targetPrice'] = t_price
            s['targetType']  = t_type
            s['upsidePct']   = upside_pct
            s['upsideRs']    = upside_rs
            # Recalculate score with updated VPB and near52High
            tech = {
                'rsi':                s.get('rsi'),
                'ema_cross':          s.get('emaCross'),
                'ema_pre_cross':      s.get('emaPreCross', False),
                'ema_trend':          s.get('emaTrend'),
                'vol_confirmed_cross':s.get('volConfirm'),
                'cross_score':        s.get('crossScore', 0),
                'ema_pullback':       s.get('emaPullback', False),
                'adx':                s.get('adx'),
                'vpb_score':          s.get('vpbScore', 0),
                'vpb_detail':         s.get('vpbDetail', 'none'),
                'near_52high':        s.get('near52High'),
                'macd':               s.get('macd'),
                'golden':             s.get('golden'),
            } if s.get('rsi') is not None else None
            sc, f, c, t, ct, l = score(s.get('pe'), s.get('debtEq'), s.get('roe'), s.get('dailyVol'), tech)
            s['score']   = sc
            s['fScore']  = f
            s['cScore']  = c
            s['tScore']  = t
            s['ctScore'] = ct
            s['lScore']  = l
            updated += 1
        except:
            pass

    ist = get_ist()
    with state_lock:
        state['stocks']       = stocks
        state['last_updated'] = ist.strftime('%d %b %Y, %I:%M %p IST')
        state['market_mode']  = get_market_mode()
        state['status']       = 'live'
    print(f"  ✅ {updated} prices updated at {ist.strftime('%H:%M:%S')} IST")
    with state_lock:
        state['ctrl']['price_update'].update({
            'last_run':        get_ist().isoformat(),
            'updated':         updated,
            'elapsed_sec':     round(time.time() - _t0, 1),
            'workers':         MAX_WORKERS,
            'batches':         len(batches),
            'batch_size':      BATCH_SIZE,
            'stocks_received': len(price_map),
            'running':         False,
        })

# ════════════════════════════════════════════════════════════════════
# EOD TECHNICAL REFRESH
# ════════════════════════════════════════════════════════════════════
def refresh_technicals():
    import threading
    with state_lock:
        stocks = list(state['stocks'])
    if not stocks:
        return

    _t0 = time.time()
    with state_lock:
        state['ctrl']['technicals']['running'] = True

    total            = len(stocks)
    stocks_by_ticker = {s['ticker']: s for s in stocks}
    updated          = 0
    done             = 0
    print(f"\n  📐 EOD: Refreshing technicals for {total} stocks (sequential history fetch)...")

    for s in stocks:
        ns = s['ticker'] + '.NS'
        try:
            time.sleep(HIST_DELAY)
            hist = yf.Ticker(ns).history(period='1y', auto_adjust=True)
            if hist is None or len(hist) < 30:
                done += 1
                continue

            tech = calc_technicals(hist)
            if not tech:
                done += 1
                continue

            vpb_rh    = tech.get('vpb_range_height', 0.0)
            mm_target = round(s['price'] + vpb_rh, 2) if vpb_rh > 0 else None
            target_price, target_type, upside_pct, upside_rs = calc_target(
                s['price'], mm_target, s.get('wk52High', 0), s.get('ath', 0)
            )
            h60 = hist.tail(60)
            sc, f, c, t, ct, l = score(s.get('pe'), s.get('debtEq'), s.get('roe'), s.get('dailyVol'), tech)
            s.update({
                'rsi':            tech['rsi'],
                'macd':           tech['macd'],
                'emaSignal':      tech['ema_signal'],
                'emaCross':       tech['ema_cross'],
                'emaCrossDays':   tech['ema_cross_days_ago'],
                'emaTrend':       tech['ema_trend'],
                'volConfirm':     tech['vol_confirmed_cross'],
                'crossScore':     tech['cross_score'],
                'emaPreCross':    tech['ema_pre_cross'],
                'emaPostCross':   tech['ema_post_cross'],
                'emaPullback':    tech['ema_pullback'],
                'golden':         tech['golden'],
                'adx':            tech['adx'],
                'vpbScore':       tech['vpb_score'],
                'vpbDetail':      tech['vpb_detail'],
                'vpbRangeHeight': tech['vpb_range_height'],
                'avg20Vol':       tech['avg20_vol'],
                'priceCoiling':   tech['price_coiling'],
                'volShrinking':   tech['vol_shrinking'],
                'near52High':     tech['near_52high'],
                'stage':          classify_stage(tech),
                'mmTarget':       mm_target,
                'targetPrice':    target_price,
                'targetType':     target_type,
                'upsidePct':      upside_pct,
                'upsideRs':       upside_rs,
                'chartDates':     [d.strftime('%Y-%m-%d') for d in h60.index],
                'chartPrices':    [round(float(p), 2) for p in h60['Close'].values],
                'score':          sc,
                'fScore':         f,
                'cScore':         c,
                'tScore':         t,
                'ctScore':        ct,
                'lScore':         l,
            })
            updated += 1
        except Exception:
            pass
        done += 1
        if done % 100 == 0:
            print(f"    ↻ technicals: {done}/{total} done, {updated} updated")

    with state_lock:
        state['stocks'] = list(stocks_by_ticker.values())
    print(f"  ✅ EOD technicals refreshed for {updated} stocks")
    with state_lock:
        state['ctrl']['technicals'].update({
            'last_run':    get_ist().isoformat(),
            'elapsed_sec': round(time.time() - _t0, 1),
            'tickers':     total,
            'updated':     updated,
            'running':     False,
        })

# ════════════════════════════════════════════════════════════════════
# FUNDAMENTAL REFRESH (PE / D/E / ROE / Sector / Name — weekly)
# ════════════════════════════════════════════════════════════════════
def refresh_fundamentals():
    with state_lock:
        stocks = list(state['stocks'])
    if not stocks:
        return

    _t0    = time.time()
    total  = len(stocks)
    done   = 0
    updated = 0
    with state_lock:
        state['ctrl']['fundamentals']['running'] = True
    print(f"\n  🔄 Refreshing fundamentals for {total} stocks (sequential, {INFO_DELAY}s delay)...")

    for s in stocks:
        time.sleep(INFO_DELAY)
        try:
            ns   = s['ticker'] + '.NS'
            info = yf.Ticker(ns).info
            pe   = float(info.get('trailingPE') or info.get('forwardPE') or 0)
            if math.isnan(pe): pe = 0.0
            roe_r = float(info.get('returnOnEquity') or 0)
            if math.isnan(roe_r): roe_r = 0.0
            deq_r = float(info.get('debtToEquity') or 0)
            if math.isnan(deq_r): deq_r = 0.0
            s['pe']      = round(pe, 1)
            s['roe']     = round(roe_r * 100, 1)
            s['debtEq']  = round(deq_r / 100, 2)
            s['sector']  = info.get('sector') or s.get('sector', 'Others')
            s['name']    = info.get('longName') or info.get('shortName') or s.get('name', s['ticker'])
            mcap_raw = info.get('marketCap', 0) or 0
            if not mcap_raw:
                shares = info.get('sharesOutstanding', 0) or 0
                if shares:
                    mcap_raw = shares * s.get('price', 0)
            if mcap_raw:
                s['mcap'] = int(round(mcap_raw / 1e7, 0))
            wk52h = float(info.get('fiftyTwoWeekHigh') or 0)
            wk52l = float(info.get('fiftyTwoWeekLow')  or 0)
            if wk52h: s['wk52High'] = round(wk52h, 2)
            if wk52l: s['wk52Low']  = round(wk52l, 2)
            tech = {
                'rsi':                s.get('rsi'),
                'ema_cross':          s.get('emaCross'),
                'ema_pre_cross':      s.get('emaPreCross', False),
                'ema_trend':          s.get('emaTrend'),
                'vol_confirmed_cross':s.get('volConfirm'),
                'cross_score':        s.get('crossScore', 0),
                'ema_pullback':       s.get('emaPullback', False),
                'adx':                s.get('adx'),
                'vpb_score':          s.get('vpbScore', 0),
                'vpb_detail':         s.get('vpbDetail', 'none'),
                'near_52high':        s.get('near52High'),
                'macd':               s.get('macd'),
                'golden':             s.get('golden'),
            } if s.get('rsi') is not None else None
            sc, f, c, t, ct, l = score(s['pe'], s['debtEq'], s['roe'], s.get('dailyVol', 0), tech)
            s['score']   = sc
            s['fScore']  = f
            s['roeWarn'] = 'high' if s['roe'] > 20 else 'medium' if s['roe'] > 12 else 'low' if s['roe'] > 0 else 'na'
            updated += 1
        except:
            pass
        done += 1
        if done % 100 == 0:
            print(f"    ↻ fundamentals: {done}/{total} done, {updated} updated")

    ist = get_ist()
    with state_lock:
        state['stocks']            = stocks
        state['last_updated']      = ist.strftime('%d %b %Y, %I:%M %p IST')
        state['fund_refreshed_at'] = ist.isoformat()
    save_cache()
    elapsed = round(time.time() - _t0, 1)
    print(f"  ✅ Fundamentals refreshed: {updated}/{total} updated in {elapsed}s")
    with state_lock:
        state['ctrl']['fundamentals'].update({
            'last_run':    ist.isoformat(),
            'elapsed_sec': elapsed,
            'yahoo_calls': done,
            'updated':     updated,
            'running':     False,
        })

# ════════════════════════════════════════════════════════════════════
# CACHE
# ════════════════════════════════════════════════════════════════════
CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cache.json')

def save_cache():
    try:
        with state_lock:
            data = {
                'stocks':           state['stocks'],
                'last_updated':     state['last_updated'],
                'saved_at':         get_ist().isoformat(),
                'fund_refreshed_at': state.get('fund_refreshed_at'),
                'ctrl':             state['ctrl'],
            }
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            f.write(_safe_json(data))
        print(f"  💾 Cache saved — {len(data['stocks'])} stocks → {CACHE_FILE}")
    except Exception as e:
        print(f"  ⚠ Cache save failed: {e}")

def load_cache():
    if not os.path.exists(CACHE_FILE):
        print("  📭 No cache — full scan needed")
        return False
    try:
        with open(CACHE_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        saved_at  = datetime.datetime.fromisoformat(data['saved_at'])
        age_hours = (get_ist() - saved_at).total_seconds() / 3600
        if age_hours > CACHE_MAX_AGE_HRS:
            print(f"  ⏰ Cache {age_hours:.1f}h old (max {CACHE_MAX_AGE_HRS}h) — rescan needed")
            return False
        stocks = data.get('stocks', [])
        if not stocks:
            print("  ⚠ Cache empty — rescan needed")
            return False
        # Re-classify stages from stored fields (picks up any classify_stage() changes)
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
            except:
                pass
        print(f"  🔄 Stages re-classified for {len(stocks)} stocks")
        saved_ctrl = data.get('ctrl') or {}
        with state_lock:
            state['stocks']            = stocks
            state['last_updated']      = data.get('last_updated','From cache')
            state['cache_saved_at']    = data.get('saved_at')
            state['fund_refreshed_at'] = data.get('fund_refreshed_at')
            state['market_mode']       = get_market_mode()
            state['status']         = 'live' if get_market_mode()=='open' else 'eod'
            state['fetch_progress'] = 100
            state['fetch_message']  = f'Loaded {len(stocks)} stocks from cache'
            state['in_range']       = len(stocks)
            state['total_scanned']  = len(stocks)
            # Restore ctrl metrics, keeping defaults for any missing keys
            for section, vals in saved_ctrl.items():
                if section in state['ctrl'] and isinstance(vals, dict):
                    state['ctrl'][section].update(vals)
        print(f"  🚀 Cache loaded — {len(stocks)} stocks (age: {age_hours:.1f}h)")
        return True
    except Exception as e:
        print(f"  ⚠ Cache load error: {e}")
        return False

# ════════════════════════════════════════════════════════════════════
# SCHEDULER
# ════════════════════════════════════════════════════════════════════
def scheduler():
    print(f"\n{'='*52}")
    print(f"  Checking for saved cache...")
    cache_ok = load_cache()

    if cache_ok:
        if get_market_mode() == 'open':
            print(f"  Market already open — refreshing prices...")
            refresh_prices()
        else:
            # Skip pre-market tech refresh if EOD already ran today (cache saved after 3:30 PM)
            with state_lock:
                raw_saved = state.get('cache_saved_at')
            eod_already_ran = False
            if raw_saved:
                try:
                    saved = datetime.datetime.fromisoformat(raw_saved)
                    today = get_ist().date()
                    eod_already_ran = (saved.date() == today and
                                       saved.hour * 60 + saved.minute >= 15 * 60 + 30)
                except:
                    pass
            if eod_already_ran:
                print(f"  Pre-market: EOD refresh already ran today — skipping tech refresh.")
            else:
                print(f"  Pre-market: refreshing technicals (RSI/EMA/ADX) before open...")
                refresh_technicals()
    else:
        print(f"  Starting full NSE scan (~45-90 min)...")
        fetch_all_stocks()
        save_cache()

    eod_saved = False
    while True:
        time.sleep(LIVE_REFRESH)
        mode = get_market_mode()
        if mode == 'open':
            refresh_prices()
            eod_saved = False
        elif mode == 'eod':
            with state_lock:
                state['status'] = 'eod'
            if not eod_saved:
                print("  Market closed — refreshing technicals then saving EOD cache...")
                refresh_technicals()
                save_cache()
                eod_saved = True
        else:
            with state_lock:
                state['status'] = 'eod'

# ════════════════════════════════════════════════════════════════════
# JSON HELPERS
# ════════════════════════════════════════════════════════════════════
def _safe_json(data):
    """Serialize to JSON, converting numpy scalars and fixing Infinity/NaN."""
    import numpy as np
    class _Enc(json.JSONEncoder):
        def default(self, o):
            if isinstance(o, np.bool_):    return bool(o)
            if isinstance(o, np.integer):  return int(o)
            if isinstance(o, np.floating): return float(o)
            return super().default(o)
    return json.dumps(data, cls=_Enc, ensure_ascii=False).replace('Infinity', 'null').replace('NaN', 'null')

# ════════════════════════════════════════════════════════════════════
# HTTP SERVER
# ════════════════════════════════════════════════════════════════════
class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args): pass  # suppress request logs

    def send_json(self, data, status=200):
        body = _safe_json(data).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type',   'application/json')
        self.send_header('Content-Length', len(body))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path, ctype):
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
        self.send_header('Access-Control-Allow-Methods', 'GET')
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path

        if path in ('/', '/index.html'):
            self.send_file('index.html', 'text/html; charset=utf-8')
            return

        if path == '/api/status':
            with state_lock:
                data = {
                    'status':         state['status'],
                    'market_mode':    state['market_mode'],
                    'last_updated':   state['last_updated'],
                    'fetch_progress': state['fetch_progress'],
                    'fetch_message':  state['fetch_message'],
                    'total_scanned':  state['total_scanned'],
                    'in_range':       state['in_range'],
                    'count':          len(state['stocks']),
                    'ist_time':       get_ist().strftime('%H:%M:%S'),
                }
            self.send_json(data)
            return

        if path == '/api/stocks':
            with state_lock:
                data = {
                    'status':       state['status'],
                    'market_mode':  state['market_mode'],
                    'last_updated': state['last_updated'],
                    'stocks':       list(state['stocks']),
                }
            self.send_json(data)
            return

        if path == '/api/prices':
            with state_lock:
                prices       = [{'ticker':s['ticker'],'price':s['price'],'change':s['change']}
                                 for s in state['stocks']]
                status       = state['status']
                last_updated = state['last_updated']
            self.send_json({
                'status':       status,
                'last_updated': last_updated,
                'prices':       prices,
            })
            return

        if path.startswith('/api/stock/'):
            ticker = path.replace('/api/stock/','').upper().strip()
            with state_lock:
                stock = next((s for s in state['stocks'] if s['ticker']==ticker), None)
            self.send_json(stock if stock else {'error':'Not found'}, 200 if stock else 404)
            return

        if path == '/api/patch_upside':
            # Patch targetPrice/upside in-memory for stocks missing it
            with state_lock:
                stocks = state['stocks']
            fixed = 0
            for s in stocks:
                if not s.get('targetPrice'):
                    price  = s.get('price') or 0
                    wk52h  = s.get('wk52High') or 0
                    mm     = s.get('mmTarget') or 0
                    candidates = []
                    if mm and float(mm) > price:   candidates.append(('MM',  round(float(mm),2)))
                    if wk52h and float(wk52h) > price: candidates.append(('52W', round(float(wk52h),2)))
                    if candidates:
                        t_type, t_price = min(candidates, key=lambda x: x[1])
                        s['targetPrice'] = t_price
                        s['targetType']  = t_type
                        s['upsidePct']   = round((t_price - price) / price * 100, 1)
                        s['upsideRs']    = round(t_price - price, 2)
                        fixed += 1
            self.send_json({'ok': True, 'fixed': fixed})
            return

        if path == '/api/rescan':
            with state_lock:
                busy = state['status'] == 'fetching'
            if busy:
                self.send_json({'ok':False,'msg':'Scan already running'})
            else:
                def do_rescan():
                    fetch_all_stocks()
                    save_cache()
                threading.Thread(target=do_rescan, daemon=True).start()
                self.send_json({'ok':True,'msg':'Full rescan started'})
            return

        if path == '/api/ctrl':
            with state_lock:
                data = dict(state['ctrl'])
            self.send_json(data)
            return

        if path == '/api/ctrl/run_prices':
            with state_lock:
                running = state['ctrl']['price_update'].get('running', False)
                no_stocks = len(state['stocks']) == 0
            if running:
                self.send_json({'ok': False, 'msg': 'Price update already running'})
            elif no_stocks:
                self.send_json({'ok': False, 'msg': 'No stocks loaded yet'})
            else:
                threading.Thread(target=refresh_prices, daemon=True).start()
                self.send_json({'ok': True, 'msg': 'Price update started'})
            return

        if path == '/api/ctrl/run_technicals':
            with state_lock:
                running = state['ctrl']['technicals'].get('running', False)
                no_stocks = len(state['stocks']) == 0
            if running:
                self.send_json({'ok': False, 'msg': 'Technical refresh already running'})
            elif no_stocks:
                self.send_json({'ok': False, 'msg': 'No stocks loaded yet'})
            else:
                def _run_tech():
                    refresh_technicals()
                    save_cache()
                threading.Thread(target=_run_tech, daemon=True).start()
                self.send_json({'ok': True, 'msg': 'Technical refresh started'})
            return

        if path == '/api/ctrl/run_fundamentals':
            with state_lock:
                running   = state['ctrl']['fundamentals'].get('running', False)
                no_stocks = len(state['stocks']) == 0
            if running:
                self.send_json({'ok': False, 'msg': 'Fundamental refresh already running'})
            elif no_stocks:
                self.send_json({'ok': False, 'msg': 'No stocks loaded yet'})
            else:
                threading.Thread(target=refresh_fundamentals, daemon=True).start()
                self.send_json({'ok': True, 'msg': 'Fundamental refresh started'})
            return

        if path == '/api/ctrl/run_ticker_fetch':
            # Ticker cache is built from a full scan — standalone MCap check is unreliable
            with state_lock:
                busy = state['status'] == 'fetching'
            if busy:
                self.send_json({'ok': False, 'msg': 'Full scan already running'})
            else:
                def _run_full():
                    fetch_all_stocks()
                    save_cache()
                threading.Thread(target=_run_full, daemon=True).start()
                self.send_json({'ok': True, 'msg': 'Full scan started — ticker cache will be rebuilt from results (~12 min)'})
            return

        if path == '/api/indices':
            try:
                import yfinance as yf
                result = {}
                for sym, name in [('^NSEI','NIFTY 50'),('^BSESN','SENSEX')]:
                    tk = yf.Ticker(sym)
                    try:
                        fi = tk.fast_info
                        last  = fi.last_price or 0
                        prev  = fi.previous_close or last
                    except Exception:
                        hist = tk.history(period='2d')
                        last = float(hist['Close'].iloc[-1]) if not hist.empty else 0
                        prev = float(hist['Close'].iloc[-2]) if len(hist) > 1 else last
                    chg   = round((last - prev) / prev * 100, 2) if prev else 0
                    result[name] = {'price': round(last, 2), 'change': chg}
                self.send_json(result)
            except Exception as e:
                self.send_json({'error': str(e)})
            return

        self.send_response(404); self.end_headers()

# ════════════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════════════
def main():
    print(f"""
==========================================
  DALAL STREET SCOUT  v6  Local Server
  MCap: Rs.{MCAP_MIN_CR:,} Cr - Rs.{MCAP_MAX_CR:,} Cr
==========================================
  Browser -> http://localhost:{PORT}
  Press Ctrl+C to stop
""")
    t = threading.Thread(target=scheduler, daemon=True)
    t.start()
    try:
        server = ThreadingHTTPServer(('0.0.0.0', PORT), Handler)
    except OSError:
        print(f"\n  ❌ Port {PORT} is already in use!")
        print(f"  Another instance is probably running.")
        print(f"  Close it first, then restart.")
        input("\n  Press Enter to exit...")
        sys.exit(1)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Server stopped.")

if __name__ == '__main__':
    main()
