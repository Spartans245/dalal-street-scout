"""
Dalal Street Scout — Local Data Server v6
==========================================
Double-click START_SERVER.bat to run.
Open browser at: http://localhost:5000
"""

import json, datetime, math, time, threading, os, sys
import warnings
warnings.filterwarnings('ignore')

from http.server import HTTPServer, BaseHTTPRequestHandler
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
MCAP_MIN_CR        = 100
MCAP_MAX_CR        = 10000
LIVE_REFRESH       = 5 * 60   # seconds between price refreshes
BATCH_DELAY        = 1.0      # seconds between stocks
CACHE_MAX_AGE_HRS  = 24

# ════════════════════════════════════════════════════════════════════
# STATE
# ════════════════════════════════════════════════════════════════════
state = {
    'stocks':         [],
    'last_updated':   None,
    'status':         'starting',
    'market_mode':    'unknown',
    'fetch_progress': 0,
    'fetch_message':  'Starting...',
    'total_scanned':  0,
    'in_range':       0,
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
def get_nse_tickers():
    try:
        headers = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://www.nseindia.com/'}
        url  = 'https://archives.nseindia.com/content/equities/EQUITY_L.csv'
        resp = requests.get(url, headers=headers, timeout=20)
        if resp.status_code == 200:
            from io import StringIO
            df = pd.read_csv(StringIO(resp.text))
            if 'SYMBOL' in df.columns:
                tickers = df['SYMBOL'].dropna().str.strip().tolist()
                print(f"  ✅ Got {len(tickers)} tickers from NSE")
                return tickers
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
        if not ema_cross and len(ema14) >= 4:
            for i in range(2, 4):
                if float(ema14.iloc[-i]) > float(ema50.iloc[-i]) and \
                   float(ema14.iloc[-i-1]) <= float(ema50.iloc[-i-1]):
                    ema_cross = True
                    break

        ema14_rising = e14n > float(ema14.iloc[-5]) if len(ema14) >= 5 else False
        ema_trend    = bool(e14n > e50n and ema14_rising and not ema_cross)

        if ema_cross:   ema_signal = 'cross'
        elif ema_trend: ema_signal = 'trend'
        else:           ema_signal = 'none'

        # Golden cross 30/200
        e30    = float(s.ewm(span=30).mean().iloc[-1])
        golden = False
        if len(c) >= 200:
            e200   = float(s.ewm(span=200).mean().iloc[-1])
            golden = bool(e30 > e200)

        # ADX proxy using ATR
        adx = 15.0
        try:
            if 'High' in hist.columns and 'Low' in hist.columns and len(c) >= 14:
                highs = hist['High'].ffill().values
                lows  = hist['Low'].ffill().values
                tr_list = []
                for i in range(1, min(15, len(c))):
                    tr = max(highs[-i]-lows[-i],
                             abs(highs[-i]-c[-i-1]),
                             abs(lows[-i]-c[-i-1]))
                    tr_list.append(tr)
                atr = sum(tr_list)/len(tr_list) if tr_list else 1
                dm  = abs(c[-1]-c[-11])/(atr*10+1e-10)*25 if len(c)>=11 else 15
                adx = round(min(50, max(5, dm)), 1)
        except:
            pass

        # Volume patterns
        vol_expand   = False
        vol_contract = False
        try:
            if 'Volume' in hist.columns and len(hist) >= 20:
                vols         = hist['Volume'].fillna(0).values
                avg20        = vols[-20:].mean()
                avg5         = vols[-5:].mean()
                vol_expand   = bool(vols[-1] > avg20 * 1.25)
                vol_contract = bool(avg5 < avg20 * 0.80)
        except:
            pass

        # Consolidation
        consolidating = False
        try:
            if len(c) >= 15:
                recent = c[-15:]
                rng    = (max(recent)-min(recent))/(min(recent)+1e-10)*100
                consolidating = bool(rng < 6.0)
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
            'vol_expand':   vol_expand,
            'vol_contract': vol_contract,
            'consolidating':consolidating,
            'near_52high':  near_52high,
        }
    except Exception as e:
        return None

# ════════════════════════════════════════════════════════════════════
# SCORING
# ════════════════════════════════════════════════════════════════════
def score(pe, debtEq, roe, dailyVol, tech):
    # Fundamentals (35 pts) — ROE not scored, warning badge only
    f = 8   # no pledge assumed — verify on screener.in

    if 0 < pe < 15:    f += 12
    elif 0 < pe < 25:  f += 9
    elif 0 < pe < 35:  f += 5
    elif 0 < pe < 50:  f += 2

    if debtEq < 0.3:   f += 10
    elif debtEq < 0.7: f += 7
    elif debtEq < 1.0: f += 4
    elif debtEq < 1.5: f += 1

    if dailyVol >= 10:  f += 5
    elif dailyVol >= 5: f += 3
    elif dailyVol >= 2: f += 1

    # Technicals (40 pts) — breakout timing
    t = 0
    if tech:
        r = tech['rsi']
        if 45 <= r <= 58:   t += 12
        elif 58 < r <= 65:  t += 7
        elif 40 <= r < 45:  t += 4
        elif 65 < r <= 72:  t += 2

        if tech['ema_cross']:         t += 12
        elif tech['ema_trend']:       t += 7

        adx = tech['adx']
        if 20 <= adx <= 35:   t += 10
        elif 15 <= adx < 20:  t += 5
        elif adx > 35:        t += 4

        if tech['vol_contract'] and tech['vol_expand']: t += 6
        elif tech['vol_expand']:   t += 3
        elif tech['vol_contract']: t += 2

        if tech['consolidating']:  t += 5
        if tech['near_52high']:    t += 3
        if tech['macd']:           t += 2
        if tech['golden']:         t += 1

    # Liquidity (10 pts)
    l = 0
    if dailyVol >= 5:    l = 10
    elif dailyVol >= 2:  l = 5
    elif dailyVol >= 0.5:l = 2

    c = 0  # catalyst — manual only for now
    return min(100, f+t+c+l), f, c, t, l

# ════════════════════════════════════════════════════════════════════
# FETCH ALL STOCKS
# ════════════════════════════════════════════════════════════════════
def fetch_all_stocks():
    with state_lock:
        state['status']         = 'fetching'
        state['fetch_progress'] = 0
        state['fetch_message']  = 'Downloading NSE ticker list...'
        state['in_range']       = 0

    tickers = get_nse_tickers()
    total   = len(tickers)

    with state_lock:
        state['total_scanned'] = total
        state['fetch_message'] = f'Scanning {total} NSE stocks...'

    results = []
    failed  = 0

    print(f"\n{'='*60}")
    print(f"  Scanning {total} NSE stocks  ₹{MCAP_MIN_CR}–{MCAP_MAX_CR} Cr MCap")
    print(f"{'='*60}\n")

    for i, ticker in enumerate(tickers):
        pct = int(i / total * 100)
        if i % 20 == 0:
            with state_lock:
                state['fetch_progress'] = pct
                state['fetch_message']  = f'Scanning {i+1} of {total}  ({len(results)} found so far)'
                state['in_range']       = len(results)

        ns = ticker.strip().replace(' ','') + '.NS'
        try:
            t    = yf.Ticker(ns)
            info = t.info

            # Fast MCap check first — skip if out of range
            mcap_raw = info.get('marketCap', 0) or 0
            mcap_cr  = round(mcap_raw / 1e7, 0)
            if mcap_cr < MCAP_MIN_CR or mcap_cr > MCAP_MAX_CR:
                continue

            # Fetch history
            hist = t.history(period='1y', auto_adjust=True)
            if hist is None or len(hist) < 30:
                continue

            # Price
            price = float(
                info.get('currentPrice') or
                info.get('regularMarketPrice') or
                float(hist['Close'].iloc[-1])
            )
            if not price or math.isnan(price) or price <= 0:
                continue

            prev   = info.get('previousClose') or (float(hist['Close'].iloc[-2]) if len(hist)>1 else price)
            change = round((price - float(prev)) / float(prev) * 100, 2) if prev else 0.0

            pe     = float(info.get('trailingPE') or info.get('forwardPE') or 0)
            if math.isnan(pe): pe = 0.0
            pe = round(pe, 1)

            roe_r  = float(info.get('returnOnEquity') or 0)
            if math.isnan(roe_r): roe_r = 0.0
            roe    = round(roe_r * 100, 1)

            deq_r  = float(info.get('debtToEquity') or 0)
            if math.isnan(deq_r): deq_r = 0.0
            debtEq = round(deq_r / 100, 2)

            avg_vol = info.get('averageVolume', 0) or 0
            dvol    = round(avg_vol * price / 1e7, 1)

            wk52h  = float(info.get('fiftyTwoWeekHigh') or 0)
            wk52l  = float(info.get('fiftyTwoWeekLow') or 0)
            pct52h = round((price - wk52h) / wk52h * 100, 1) if wk52h else 0
            pct52l = round((price - wk52l) / wk52l * 100, 1) if wk52l else 0

            sector = info.get('sector') or 'Others'
            name   = info.get('longName') or info.get('shortName') or ticker

            tech   = calc_technicals(hist)
            sc, f, c, t2, l = score(pe, debtEq, roe, dvol, tech)

            roe_warn = 'high' if roe > 20 else 'medium' if roe > 12 else 'low' if roe > 0 else 'na'

            # 60-day chart — safe extraction
            chart_prices = []
            chart_dates  = []
            try:
                h60 = hist.tail(60)
                closes = h60['Close'].ffill().tolist()
                chart_prices = [round(float(x), 2) for x in closes if not math.isnan(float(x))]
                chart_dates  = [str(d.date()) for d in h60.index.tolist()]
            except:
                pass

            results.append({
                'ticker':        ticker,
                'name':          name,
                'sector':        sector,
                'price':         round(price, 2),
                'change':        change,
                'pe':            pe,
                'mcap':          int(mcap_cr),
                'promoterHolding': 0,
                'pledging':      0,
                'debtEq':        debtEq,
                'roe':           roe,
                'roeWarn':       roe_warn,
                'wk52High':      round(wk52h, 2),
                'wk52Low':       round(wk52l, 2),
                'pctFrom52High': pct52h,
                'pctFrom52Low':  pct52l,
                'rsi':           tech['rsi']           if tech else 50.0,
                'adx':           tech['adx']           if tech else 15.0,
                'macd':          tech['macd']          if tech else False,
                'emaSignal':     tech['ema_signal']    if tech else 'none',
                'emaCross':      tech['ema_cross']     if tech else False,
                'emaTrend':      tech['ema_trend']     if tech else False,
                'golden':        tech['golden']        if tech else False,
                'volExpand':     tech['vol_expand']    if tech else False,
                'volContract':   tech['vol_contract']  if tech else False,
                'consolidating': tech['consolidating'] if tech else False,
                'near52High':    tech['near_52high']   if tech else False,
                'volConfirm':    tech['vol_expand']    if tech else False,
                'catalysts':     [],
                'dailyVol':      dvol,
                'score':         sc,
                'fScore':        f,
                'cScore':        c,
                'tScore':        t2,
                'lScore':        l,
                'chartPrices':   chart_prices,
                'chartDates':    chart_dates,
            })

            star = '⭐' if sc >= 65 else '  '
            print(f"  ✅ {ticker:<16} ₹{price:>9,.2f}  Score:{sc:>3}  {star}")

        except Exception as e:
            failed += 1

        time.sleep(BATCH_DELAY)

    ist = get_ist()
    strong = [s for s in results if s['score'] >= 65]

    with state_lock:
        state['stocks']         = results
        state['last_updated']   = ist.strftime('%d %b %Y, %I:%M %p IST')
        state['market_mode']    = get_market_mode()
        state['status']         = 'live' if get_market_mode() == 'open' else 'eod'
        state['fetch_progress'] = 100
        state['fetch_message']  = f'Done — {len(results)} stocks in range'
        state['in_range']       = len(results)
        state['total_scanned']  = total

    print(f"\n{'='*60}")
    print(f"  ✅ {len(results)} stocks in ₹{MCAP_MIN_CR}–{MCAP_MAX_CR} Cr range")
    print(f"  ⭐ {len(strong)} strong entry candidates (score 65+)")
    print(f"  ❌ {failed} tickers failed / not found")
    print(f"  📡 Browser: http://localhost:{PORT}")
    print(f"{'='*60}\n")

# ════════════════════════════════════════════════════════════════════
# PRICE REFRESH (market hours — fast, no history re-fetch)
# ════════════════════════════════════════════════════════════════════
def refresh_prices():
    with state_lock:
        stocks = list(state['stocks'])
    if not stocks:
        return

    print(f"  🔄 Refreshing {len(stocks)} prices...")
    updated = 0
    for s in stocks:
        try:
            fi    = yf.Ticker(s['ticker']+'.NS').fast_info
            price = getattr(fi,'last_price',None) or getattr(fi,'regular_market_price',None)
            if price and not math.isnan(float(price)):
                prev   = getattr(fi,'previous_close',None) or s['price']
                change = round((float(price)-float(prev))/float(prev)*100,2) if prev else s['change']
                s['price']  = round(float(price), 2)
                s['change'] = change
                updated += 1
            time.sleep(0.15)
        except:
            pass

    ist = get_ist()
    with state_lock:
        state['stocks']       = stocks
        state['last_updated'] = ist.strftime('%d %b %Y, %I:%M %p IST')
        state['market_mode']  = get_market_mode()
        state['status']       = 'live'
    print(f"  ✅ {updated} prices updated at {ist.strftime('%H:%M:%S')} IST")

# ════════════════════════════════════════════════════════════════════
# CACHE
# ════════════════════════════════════════════════════════════════════
CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cache.json')

def save_cache():
    try:
        with state_lock:
            data = {
                'stocks':       state['stocks'],
                'last_updated': state['last_updated'],
                'saved_at':     get_ist().isoformat(),
            }
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)
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
        with state_lock:
            state['stocks']         = stocks
            state['last_updated']   = data.get('last_updated','From cache')
            state['market_mode']    = get_market_mode()
            state['status']         = 'live' if get_market_mode()=='open' else 'eod'
            state['fetch_progress'] = 100
            state['fetch_message']  = f'Loaded {len(stocks)} stocks from cache'
            state['in_range']       = len(stocks)
            state['total_scanned']  = len(stocks)
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
        print(f"  Refreshing prices from cache...")
        if get_market_mode() == 'open':
            refresh_prices()
        else:
            print(f"  Market {get_market_mode()} — using cached prices")
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
                print("  Market closed — saving EOD cache...")
                save_cache()
                eod_saved = True
        else:
            with state_lock:
                state['status'] = 'eod'

# ════════════════════════════════════════════════════════════════════
# HTTP SERVER
# ════════════════════════════════════════════════════════════════════
class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args): pass  # suppress request logs

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
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
                self.send_json({
                    'status':         state['status'],
                    'market_mode':    state['market_mode'],
                    'last_updated':   state['last_updated'],
                    'fetch_progress': state['fetch_progress'],
                    'fetch_message':  state['fetch_message'],
                    'total_scanned':  state['total_scanned'],
                    'in_range':       state['in_range'],
                    'count':          len(state['stocks']),
                    'ist_time':       get_ist().strftime('%H:%M:%S'),
                })
            return

        if path == '/api/stocks':
            with state_lock:
                self.send_json({
                    'status':       state['status'],
                    'market_mode':  state['market_mode'],
                    'last_updated': state['last_updated'],
                    'stocks':       state['stocks'],
                })
            return

        if path == '/api/prices':
            with state_lock:
                prices = [{'ticker':s['ticker'],'price':s['price'],'change':s['change']}
                          for s in state['stocks']]
                self.send_json({
                    'status':       state['status'],
                    'last_updated': state['last_updated'],
                    'prices':       prices,
                })
            return

        if path.startswith('/api/stock/'):
            ticker = path.replace('/api/stock/','').upper().strip()
            with state_lock:
                stock = next((s for s in state['stocks'] if s['ticker']==ticker), None)
            self.send_json(stock if stock else {'error':'Not found'}, 200 if stock else 404)
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

        self.send_response(404); self.end_headers()

# ════════════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════════════
def main():
    print(f"""
╔══════════════════════════════════════════════════╗
║      DALAL STREET SCOUT  v6  Local Server        ║
║      MCap: ₹{MCAP_MIN_CR:,} Cr – ₹{MCAP_MAX_CR:,} Cr              ║
╚══════════════════════════════════════════════════╝
  Browser → http://localhost:{PORT}
  Press Ctrl+C to stop
""")
    t = threading.Thread(target=scheduler, daemon=True)
    t.start()
    server = HTTPServer(('0.0.0.0', PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Server stopped.")

if __name__ == '__main__':
    main()
