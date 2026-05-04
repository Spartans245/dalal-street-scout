"""
backfill_st_tickers.py — one-shot backfill for tickers that moved to -ST segment.
Fetches recent OHLCV from Kite for each ticker, patches signals_log.json prices,
then re-runs evals_db_writer to sync DB.

Run once: python backfill_st_tickers.py
"""
import sys, os, json, time, datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

SIGNALS_FILE = os.path.join(BASE_DIR, 'data', 'signals_log.json')
KITE_INST    = os.path.join(BASE_DIR, 'kite', 'instruments.json')
TOKEN_FILE   = os.path.join(BASE_DIR, 'kite_token.json')
HIST_DELAY   = 0.35

# ── Load Kite session ──────────────────────────────────────────────────
try:
    from kiteconnect import KiteConnect
    with open(TOKEN_FILE) as f:
        tok = json.load(f)
    with open(os.path.join(BASE_DIR, 'kite_config.json')) as f:
        cfg = json.load(f)
    kite = KiteConnect(api_key=cfg['api_key'])
    kite.set_access_token(tok['access_token'])
    print('[BACKFILL] Kite session ready')
except Exception as e:
    print(f'[BACKFILL] ERROR: cannot init Kite — {e}')
    sys.exit(1)

# ── Load instrument token map ──────────────────────────────────────────
with open(KITE_INST) as f:
    inst = json.load(f)
token_map = inst.get('map', {})

# ── Tickers to backfill (found with -ST or -SM suffix) ─────────────────
BE_TICKERS = ['PREMIERPOL','SHIVAUM','DBOL','MAGNUM','REPL','AFFORDABLE','AKSHOPTFBR',
              'BROOKS','CTE','DCI','DUCON','FCSSOFT','JHS','KREBSBIO','LIKHITHA','LPDC',
              'LYKALABS','MADHUCON','MBLINFRA','PNC','RACE','RADIOCITY','SAKUMA','SECURKLOUD',
              'SETCO','VIKASECO','VIKASLIFE','ANIKINDS','GTL','KAMOPAINTS','RAJMET','SOFTTECH',
              'UMAEXPORTS','URAVIDEF','ARTNIRMAN','ASTRON','BAFNAPH','BANKA','GSS','HINDCON',
              'MCLEODRUSS','PRITI','SAMBHAAV','SECMARK','SUVIDHAA','VIRINCHI','VIVIMEDLAB',
              'PARSVNATH','ACCURACY','HAVISHA','BYKE','FILATFASH','HILINFRA','KECL','PAVNAIND',
              'SUMIT','VISHWARAJ','VITAL','BALAXI','DISHTV','GLFL','JKIPL','TOKYOPLAST']

targets = [(t, token_map.get(t+'-BE'), t+'-BE') for t in BE_TICKERS if token_map.get(t+'-BE')]

print(f'[BACKFILL] {len(targets)} tickers to fetch')

# ── Fetch OHLCV for each ───────────────────────────────────────────────
today   = datetime.date.today()
from_dt = today - datetime.timedelta(days=30)

fetched = {}  # ticker -> {date: close}
for ticker, token, kite_sym in targets:
    try:
        time.sleep(HIST_DELAY)
        records = kite.historical_data(token, from_date=from_dt, to_date=today, interval='day')
        closes = {}
        for r in records:
            d = r['date']
            if hasattr(d, 'date'):
                d = d.date().isoformat()
            else:
                d = str(d)[:10]
            closes[d] = round(float(r['close']), 2)
        fetched[ticker] = closes
        print(f'  {ticker} ({kite_sym}): {len(closes)} days, latest={max(closes) if closes else "none"}')
    except Exception as e:
        print(f'  {ticker} ({kite_sym}): ERROR — {e}')

print(f'[BACKFILL] Fetched data for {len(fetched)} tickers')

# ── Manual patch: VSTTILLERS Apr 21 (lost when manual run clobbered scheduled BAT output) ──
# Apr 21 close from kite raw: 5278.1
fetched.setdefault('VSTTILLERS', {})['2026-04-21'] = 5278.1
print('[BACKFILL] VSTTILLERS 2026-04-21 patched manually from kite raw data')

# ── Patch signals_log.json ─────────────────────────────────────────────
with open(SIGNALS_FILE, encoding='utf-8') as f:
    payload = json.load(f)
signals = payload['signals']

patched_signals = 0
patched_entries = 0

for sig in signals:
    if sig.get('outcome') != 'OPEN':
        continue
    ticker = sig.get('ticker')
    if ticker not in fetched:
        continue

    closes  = fetched[ticker]
    prices  = sig.setdefault('prices', {})
    latest  = max(prices.keys()) if prices else '1970-01-01'

    added = 0
    for date, close in closes.items():
        if date > latest and date not in prices:
            prices[date] = close
            added += 1

    if added:
        patched_signals += 1
        patched_entries += added

print(f'[BACKFILL] Patched {patched_signals} signals, {patched_entries} price entries added')

with open(SIGNALS_FILE, 'w', encoding='utf-8') as f:
    json.dump(payload, f, separators=(',', ':'))
print('[BACKFILL] signals_log.json saved')

# ── Sync to DB ─────────────────────────────────────────────────────────
import subprocess
result = subprocess.run(
    [sys.executable, '-W', 'ignore', os.path.join(BASE_DIR, 'evals_db_writer.py')],
    cwd=BASE_DIR
)
if result.returncode == 0:
    print('[BACKFILL] DB synced')
else:
    print('[BACKFILL] WARN: evals_db_writer failed')

print('[BACKFILL] Done')
