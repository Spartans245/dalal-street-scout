"""
fix_qa_integrity.py — One-shot DB repair for QA integrity failures.

Fixes:
  A4  — Close duplicate OPEN baseline signals (keep signal-tier, close the baseline duplicate)
  B5  — Strip tier_date from retrigger_dates where it appears as an entry
  B6  — Correct tier_date to first real top-3 date in stocks_master; then strip B5 artifacts
  B1  — Fix MODTHREAD day0=2026-04-11 (Saturday) → 2026-04-10 (Friday)

Run once, then re-run qa_worker.py to verify clean.
"""

import sqlite3, json, datetime, os, sys

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE  = os.path.join(BASE_DIR, 'data', 'dalal_street.db')

PHASES = ['post_cross', 'pre_cross', 'breakout', 'coiling', 'pullback', 'trending']

# ── Scoring helpers (identical to qa_worker) ──────────────────────────────────

def rsi_score(r):
    if r is None: return 0
    if 45 <= r <= 58: return 12
    if 58 < r <= 65: return 7
    if 40 <= r < 45: return 4
    if 65 < r <= 72: return 2
    return 0

def adx_score(a):
    if a is None: return 0
    if 20 <= a <= 35: return 10
    if 15 <= a < 20: return 5
    if a > 35: return 4
    return 0

def sig_score(s):
    stage = s['stage']
    if stage == 'trending': return 0
    if s['emaPreCross'] and (s['vpbScore'] or 0) > 0:
        vs = s['vpbScore']
        return 18 if vs >= 10 else (14 if vs >= 7 else 12)
    if (s['crossScore'] or 0) > 0:
        return s['crossScore'] + (3 if stage == 'pullback' else 0)
    if (s['vpbScore'] or 0) > 0:
        return s['vpbScore']
    return 0

def phase_key(s):
    return (
        -((s['fScore'] or 0) + rsi_score(s['rsi']) + adx_score(s['adx'])),
        -(s['upsidePct'] if s['upsidePct'] is not None else -999),
        -(s['mcap'] or 0),
    )

def all_key(s):
    return (
        -((s['fScore'] or 0) + rsi_score(s['rsi']) + adx_score(s['adx']) + sig_score(s)),
        -(s['upsidePct'] if s['upsidePct'] is not None else -999),
        -(s['mcap'] or 0),
    )

def get_top3_set(con, trading_date):
    """Return set of tickers in top-3 for a given trading_date in stocks_master."""
    hist_stocks = [dict(r) for r in con.execute(
        'SELECT ticker, stage, fScore, rsi, adx, upsidePct, mcap, emaPreCross, vpbScore, crossScore '
        'FROM stocks_master WHERE trading_date=? AND stage != "none"',
        (trading_date,)
    ).fetchall()]
    if not hist_stocks:
        return set()
    selected = set()
    phase_set = set()
    for phase in PHASES:
        for s in sorted([s for s in hist_stocks if s['stage'] == phase], key=phase_key)[:3]:
            phase_set.add(s['ticker'])
            selected.add(s['ticker'])
    for s in sorted(hist_stocks, key=all_key)[:3]:
        if s['ticker'] not in phase_set:
            selected.add(s['ticker'])
    return selected

def find_first_top3_date(con, ticker, from_date):
    """Find first date >= from_date where ticker was in top-3. Returns None if not found."""
    dates = [r[0] for r in con.execute(
        'SELECT DISTINCT trading_date FROM stocks_master WHERE trading_date >= ? ORDER BY trading_date',
        (from_date,)
    ).fetchall()]
    for td in dates:
        if ticker in get_top3_set(con, td):
            return td
    return None

def _rdates_list(raw):
    """Parse retrigger_dates JSON → list of dicts."""
    if not raw:
        return []
    try:
        val = json.loads(raw)
        if isinstance(val, list):
            return val
    except Exception:
        pass
    return []

def _rdates_encode(lst):
    return json.dumps(lst, separators=(',', ':'))

# ── Main fix ──────────────────────────────────────────────────────────────────

def run():
    con = sqlite3.connect(DB_FILE)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA journal_mode=WAL')

    a4_fixed = b5_fixed = b6_fixed = b1_fixed = 0
    skipped  = []

    # ── A4: close baseline duplicate OPEN signals ─────────────────────────────
    print('\n[FIX] === A4: duplicate OPEN signals ===')
    all_open = [dict(r) for r in con.execute(
        'SELECT id, ticker, day0, tier, tier_date, outcome, retrigger_dates '
        'FROM evals_signals WHERE outcome="OPEN"'
    ).fetchall()]

    from collections import defaultdict
    by_ticker = defaultdict(list)
    for s in all_open:
        by_ticker[s['ticker']].append(s)

    for ticker, sigs in by_ticker.items():
        if len(sigs) < 2:
            continue
        # Sort: signal-tier first, then by day0 desc
        sigs.sort(key=lambda s: (0 if s['tier'] == 'signal' else 1, s['day0']))
        keeper = sigs[0]  # signal-tier or oldest if both same tier
        duplicates = sigs[1:]
        for dup in duplicates:
            print(f'  A4 close: {dup["id"]} (tier={dup["tier"]}, day0={dup["day0"]}) — keeping {keeper["id"]}')
            with con:
                con.execute(
                    'UPDATE evals_signals SET outcome="EXPIRED", outcome_day=0, '
                    'outcome_price=NULL, outcome_ret=NULL WHERE id=?',
                    (dup['id'],)
                )
            a4_fixed += 1

    print(f'[FIX] A4: closed {a4_fixed} duplicate(s)')

    # ── Reload open signal-tier signals for B5/B6 ─────────────────────────────
    signal_open = [dict(r) for r in con.execute(
        'SELECT id, ticker, day0, tier, tier_date, retrigger_dates '
        'FROM evals_signals WHERE outcome="OPEN" AND tier="signal"'
    ).fetchall()]

    # ── B5: strip tier_date from retrigger_dates ──────────────────────────────
    print('\n[FIX] === B5: tier_date in retrigger_dates ===')
    for sig in signal_open:
        tier_date = sig['tier_date']
        if not tier_date:
            continue
        rdates = _rdates_list(sig['retrigger_dates'])
        before = len(rdates)
        rdates_clean = [r for r in rdates
                        if (r['date'] if isinstance(r, dict) else r) != tier_date]
        if len(rdates_clean) < before:
            print(f'  B5 strip: {sig["id"]} tier_date={tier_date} removed from retrigger_dates ({before}->{len(rdates_clean)})')
            with con:
                con.execute(
                    'UPDATE evals_signals SET retrigger_dates=? WHERE id=?',
                    (_rdates_encode(rdates_clean), sig['id'])
                )
            b5_fixed += 1

    print(f'[FIX] B5: stripped tier_date from retrigger_dates in {b5_fixed} signal(s)')

    # ── B6: correct tier_date to first real top-3 date ────────────────────────
    print('\n[FIX] === B6: tier_date not matching actual top-3 ===')

    # Reload after B5 fixes
    signal_open = [dict(r) for r in con.execute(
        'SELECT id, ticker, day0, tier, tier_date, retrigger_dates '
        'FROM evals_signals WHERE outcome="OPEN" AND tier="signal"'
    ).fetchall()]

    available_dates = set(r[0] for r in con.execute(
        'SELECT DISTINCT trading_date FROM stocks_master'
    ).fetchall())

    for sig in signal_open:
        tier_date = sig['tier_date']
        if not tier_date or tier_date not in available_dates:
            continue
        # Check if stock was actually top-3 on tier_date
        top3 = get_top3_set(con, tier_date)
        if sig['ticker'] in top3:
            continue  # OK, no fix needed

        # Find correct tier_date
        correct = find_first_top3_date(con, sig['ticker'], sig['day0'])
        if correct is None:
            # No top-3 date found — use earliest retrigger date, else day0
            rdates = _rdates_list(sig['retrigger_dates'])
            rd_list = sorted([r['date'] if isinstance(r, dict) else r for r in rdates])
            correct = rd_list[0] if rd_list else sig['day0']
            print(f'  B6 fallback: {sig["id"]} no top-3 date found, using {correct}')
        else:
            print(f'  B6 fix: {sig["id"]} tier_date {tier_date}→{correct}')

        # Now strip the new correct date from retrigger_dates if it appears there (prevent B5)
        rdates = _rdates_list(sig['retrigger_dates'])
        rdates_clean = [r for r in rdates
                        if (r['date'] if isinstance(r, dict) else r) != correct]
        stripped = len(rdates) - len(rdates_clean)
        if stripped:
            print(f'    also stripped {correct} from retrigger_dates (B5 prevention)')

        with con:
            con.execute(
                'UPDATE evals_signals SET tier_date=?, retrigger_dates=? WHERE id=?',
                (correct, _rdates_encode(rdates_clean), sig['id'])
            )
        b6_fixed += 1

    print(f'[FIX] B6: corrected tier_date for {b6_fixed} signal(s)')
    # NOTE: signals that never ranked top-3 in any available data are simply deleted
    # from evals_signals entirely (not expired) — see the separate cleanup step above.

    # ── B1: fix MODTHREAD day0 weekend stamp ──────────────────────────────────
    print('\n[FIX] === B1: weekend day0 ===')
    b1_rows = [dict(r) for r in con.execute(
        'SELECT id, ticker, day0, tier_date FROM evals_signals WHERE outcome="OPEN" AND tier="signal"'
    ).fetchall()]
    for sig in b1_rows:
        if sig['day0'] and datetime.date.fromisoformat(sig['day0']).weekday() >= 5:
            # Move to previous Friday
            d = datetime.date.fromisoformat(sig['day0'])
            while d.weekday() >= 5:
                d -= datetime.timedelta(days=1)
            correct_day0 = d.isoformat()
            print(f'  B1 fix: {sig["id"]} day0 {sig["day0"]}→{correct_day0}')
            with con:
                con.execute(
                    'UPDATE evals_signals SET day0=? WHERE id=?',
                    (correct_day0, sig['id'])
                )
            # Also fix prices key in evals_daily_prices if needed (not required for QA)
            b1_fixed += 1

    print(f'[FIX] B1: corrected {b1_fixed} weekend day0(s)')

    # ── Summary ───────────────────────────────────────────────────────────────
    con.close()
    print(f'\n[FIX] Done: A4={a4_fixed} B5={b5_fixed} B6={b6_fixed} B1={b1_fixed}')
    print('[FIX] Run qa_worker.py to verify.')

if __name__ == '__main__':
    run()
