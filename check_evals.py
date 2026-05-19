"""
check_evals.py — Post-evals check: verify top-3 stocks per phase (+ ALL tab extra)
are correctly reflected in evals_signals.

For each stock:
  - If not in evals_signals at all → MISSING (should have been logged as new)
  - If exists with tier != 'signal' → NOT SIGNAL TIER
  - If exists with outcome != 'OPEN' → CLOSED (outcome already set, skip)
  - If exists, tier='signal', outcome='OPEN', but today's date not in retrigger_dates → MISSING RETRIGGER
  - If exists, tier='signal', outcome='OPEN', today's date in retrigger_dates (or tier_date==today) → OK

Run AFTER eval_worker has completed for today.
"""
import sqlite3, json, os

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'dalal_street.db')

PHASES = ['post_cross', 'pre_cross', 'breakout', 'coiling', 'pullback', 'trending']

def rsi_score(r):
    if r is None: return 0
    if 45 <= r <= 58: return 12
    if 58 < r <= 65:  return 7
    if 40 <= r < 45:  return 4
    if 65 < r <= 72:  return 2
    return 0

def adx_score(a):
    if a is None: return 0
    if 20 <= a <= 35: return 10
    if 15 <= a < 20:  return 5
    if a > 35:        return 4
    return 0

def sig_score(s):
    if s['stage'] == 'trending': return 0
    if s['emaPreCross'] and (s['vpbScore'] or 0) > 0:
        vs = s['vpbScore']
        return 18 if vs >= 10 else (14 if vs >= 7 else 12)
    if (s['crossScore'] or 0) > 0:
        return s['crossScore'] + (3 if s['stage'] == 'pullback' else 0)
    if (s['vpbScore'] or 0) > 0:
        return s['vpbScore']
    return 0

con = sqlite3.connect(f'file:{DB}?mode=ro', uri=True)
con.row_factory = sqlite3.Row

trading_date = con.execute('SELECT DISTINCT trading_date FROM stocks_live LIMIT 1').fetchone()[0]

stocks = [dict(r) for r in con.execute(
    "SELECT ticker, stage, fScore, rsi, adx, upsidePct, mcap, emaPreCross, vpbScore, crossScore "
    "FROM stocks_live WHERE stage != 'none'"
).fetchall()]

# Build top-3 per phase
selected = {}  # ticker -> phase
phase_tickers = set()
for phase in PHASES:
    bucket = sorted(
        [s for s in stocks if s['stage'] == phase],
        key=lambda s: (
            -((s['fScore'] or 0) + rsi_score(s['rsi']) + adx_score(s['adx'])),
            -(s['upsidePct'] if s['upsidePct'] is not None else -999),
            -(s['mcap'] or 0)
        )
    )[:3]
    for s in bucket:
        phase_tickers.add(s['ticker'])
        selected[s['ticker']] = phase

# ALL tab top-3, remove phase duplicates
all_sorted = sorted(stocks, key=lambda s: (
    -((s['fScore'] or 0) + rsi_score(s['rsi']) + adx_score(s['adx']) + sig_score(s)),
    -(s['upsidePct'] if s['upsidePct'] is not None else -999),
    -(s['mcap'] or 0)
))
for s in all_sorted[:3]:
    if s['ticker'] not in phase_tickers:
        selected[s['ticker']] = 'all_tab'

# Load evals_signals for these tickers
placeholders = ','.join('?' * len(selected))
evals_rows = {r['ticker']: dict(r) for r in con.execute(
    f"SELECT ticker, tier, tier_date, outcome, retrigger_dates FROM evals_signals WHERE ticker IN ({placeholders})",
    list(selected.keys())
).fetchall()}
con.close()

print(f"Trading date : {trading_date}")
print(f"Stocks checked: {len(selected)}\n")

header = f"{'PHASE':<12} {'TICKER':<15} {'STATUS':<12} {'ENTRY TYPE':<14} {'DETAIL'}"
print(header)
print("-" * len(header))

issues = 0
for ticker, phase in sorted(selected.items(), key=lambda x: (x[1], x[0])):
    row = evals_rows.get(ticker)

    if row is None:
        status, entry, detail = "ISSUE", "—", "not in evals_signals"
        issues += 1
    elif row['outcome'] != 'OPEN':
        status, entry, detail = "CLOSED", "—", f"outcome={row['outcome']}"
    elif row['tier'] != 'signal':
        status, entry, detail = "ISSUE", "—", f"tier={row['tier']} (not signal)"
        issues += 1
    else:
        tier_date = row['tier_date']
        retriggers = json.loads(row['retrigger_dates'] or '[]')
        retrigger_dates = {r['date'] for r in retriggers}
        if tier_date == trading_date:
            status, entry, detail = "OK", "NEW ENTRY", f"tier_date={tier_date}"
        elif trading_date in retrigger_dates:
            status, entry, detail = "OK", "RETRIGGER", f"tier_date={tier_date}"
        else:
            status, entry, detail = "ISSUE", "MISSING", f"tier_date={tier_date}, no retrigger for {trading_date}"
            issues += 1

    print(f"{phase:<12} {ticker:<15} {status:<12} {entry:<14} {detail}")

print(f"\n{'All OK' if issues == 0 else f'{issues} issue(s) found'}")
