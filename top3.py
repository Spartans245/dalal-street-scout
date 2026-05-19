"""
top3.py — Extract top-3 tickers per phase exactly as shown on the home screen.

Source: stocks_live table in data/dalal_street.db
Sort:   calcPipeScore = fScore + rsiScore(rsi) + adxScore(adx) desc
        → upsidePct desc → mcap desc
Matches index.html:4309 calcPipeScore exactly.
"""
import sqlite3, os

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'dalal_street.db')

PHASES = ['post_cross', 'pre_cross', 'breakout', 'coiling', 'pullback', 'trending']

# Exact bracket lookups from index.html:3469-3470
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
    # Exact logic from index.html:4670-4677
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
rows = con.execute(
    "SELECT ticker, stage, fScore, rsi, adx, upsidePct, mcap, "
    "emaPreCross, vpbScore, crossScore "
    "FROM stocks_live WHERE stage != 'none'"
).fetchall()
con.close()

stocks = [dict(r) for r in rows]

for phase in PHASES:
    bucket = [s for s in stocks if s['stage'] == phase]
    bucket.sort(key=lambda s: (
        -((s['fScore'] or 0) + rsi_score(s['rsi']) + adx_score(s['adx'])),
        -(s['upsidePct'] if s['upsidePct'] is not None else -999),
        -(s['mcap'] or 0)
    ))
    top3 = bucket[:3]
    label = phase.upper().replace('_', '-')
    print(f"\n{label} ({len(bucket)} stocks)")
    for i, s in enumerate(top3, 1):
        ft = (s['fScore'] or 0) + rsi_score(s['rsi']) + adx_score(s['adx'])
        print(f"  #{i}: {s['ticker']:<15}  F+T={ft}  upside={s['upsidePct'] or 0:.1f}%")

# ALL tab top 3: F+T+S desc → upsidePct desc → mcap desc
# Remove any that are already in a phase top-3; keep only the extras
phase_tickers = {s['ticker'] for phase in PHASES
                 for s in sorted([x for x in stocks if x['stage'] == phase],
                     key=lambda s: (
                         -((s['fScore'] or 0) + rsi_score(s['rsi']) + adx_score(s['adx'])),
                         -(s['upsidePct'] if s['upsidePct'] is not None else -999),
                         -(s['mcap'] or 0)))[:3]}

all_sorted = sorted(stocks, key=lambda s: (
    -((s['fScore'] or 0) + rsi_score(s['rsi']) + adx_score(s['adx']) + sig_score(s)),
    -(s['upsidePct'] if s['upsidePct'] is not None else -999),
    -(s['mcap'] or 0)
))
# From the all-tab top-3, drop any already in a phase top-3
all_top3 = all_sorted[:3]
remaining = [s for s in all_top3 if s['ticker'] not in phase_tickers]

print(f"\nALL — top-3 after removing phase duplicates")
if remaining:
    for i, s in enumerate(remaining, 1):
        fts = (s['fScore'] or 0) + rsi_score(s['rsi']) + adx_score(s['adx']) + sig_score(s)
        print(f"  #{i}: {s['ticker']:<15}  F+T+S={fts}  stage={s['stage']}  upside={s['upsidePct'] or 0:.1f}%")
else:
    print("  (all covered by phase selections)")
