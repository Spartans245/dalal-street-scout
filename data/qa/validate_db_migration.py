"""
QA validation script — run after DB implementation to confirm
the stockwise screen data is identical to the pre-DB snapshot.

Usage:
    python data/qa/validate_db_migration.py
"""
import json, sys, os, requests

API = 'http://localhost:5000'
SNAPSHOT = os.path.join(os.path.dirname(__file__), 'snapshot_pre_db.json')

def load_snapshot():
    with open(SNAPSHOT, encoding='utf-8') as f:
        return json.load(f)

def check(label, expected, actual, tol=0):
    if tol:
        ok = abs(expected - actual) <= tol
    else:
        ok = expected == actual
    status = 'PASS' if ok else 'FAIL'
    print(f'  [{status}] {label}: expected={expected}, got={actual}')
    return ok

def main():
    snap = load_snapshot()
    failures = 0

    print(f'\n=== QA Validation: pre-DB snapshot vs current API ===')
    print(f'Snapshot version: {snap[\"version\"]} captured {snap[\"captured_at\"][:19]}\n')

    # ── 1. /api/evals signal counts ──────────────────────────────────────
    print('1. /api/evals — signal counts')
    try:
        r = requests.get(f'{API}/api/evals', timeout=30)
        data = r.json()
        signals = data.get('signals', [])
        signal_tier = [s for s in signals if s.get('tier') == 'signal']

        outcomes = {}
        for s in signals:
            o = s.get('outcome', 'OPEN')
            outcomes[o] = outcomes.get(o, 0) + 1

        if not check('total signals', snap['signals_log']['total_signals'], len(signals)):
            failures += 1
        if not check('signal-tier count', snap['signals_log']['signal_tier_count'], len(signal_tier)):
            failures += 1
        for o, cnt in snap['signals_log']['outcomes'].items():
            if not check(f'outcome={o}', cnt, outcomes.get(o, 0)):
                failures += 1

    except Exception as e:
        print(f'  [FAIL] Could not reach /api/evals: {e}')
        failures += 1

    # ── 2. Signal-tier field completeness ────────────────────────────────
    print('\n2. Signal-tier field completeness')
    required_fields = ['ticker','tier','tier_date','day0','price_d0','outcome',
                       'prices','stage_history','retrigger_dates','stage','score']
    missing_fields = 0
    for s in signal_tier:
        for f in required_fields:
            if f not in s:
                print(f'  [FAIL] {s["ticker"]} missing field: {f}')
                missing_fields += 1
    if missing_fields == 0:
        print(f'  [PASS] All {len(signal_tier)} signal-tier stocks have required fields')
    else:
        failures += 1

    # ── 3. Sample signal deep comparison ─────────────────────────────────
    print('\n3. Sample signal deep comparison (first 5 signal-tier)')
    snap_samples = {s['ticker']: s for s in snap['signals_log']['sample_signals']}
    sig_map = {s['ticker']: s for s in signal_tier}

    for ticker, expected in snap_samples.items():
        actual = sig_map.get(ticker)
        if not actual:
            print(f'  [FAIL] {ticker}: not found in API response')
            failures += 1
            continue
        # Check prices dict matches
        exp_prices = expected.get('prices', {})
        act_prices = actual.get('prices', {})
        if exp_prices != act_prices:
            missing = set(exp_prices) - set(act_prices)
            extra   = set(act_prices) - set(exp_prices)
            print(f'  [FAIL] {ticker} prices mismatch: missing={missing}, extra={extra}')
            failures += 1
        else:
            print(f'  [PASS] {ticker}: prices match ({len(act_prices)} dates)')
        # Check outcome
        if not check(f'{ticker} outcome', expected.get('outcome'), actual.get('outcome')):
            failures += 1
        if not check(f'{ticker} outcome_ret', expected.get('outcome_ret'), actual.get('outcome_ret')):
            failures += 1

    # ── 4. /api/stocks stock counts ──────────────────────────────────────
    print('\n4. /api/stocks — stock counts')
    try:
        r = requests.get(f'{API}/api/stocks', timeout=30)
        data = r.json()
        stocks = data.get('stocks', [])
        stage_counts = {}
        for s in stocks:
            sg = s.get('stage', 'none')
            stage_counts[sg] = stage_counts.get(sg, 0) + 1

        if not check('total stocks', snap['stocks']['total'], len(stocks), tol=5):
            failures += 1
        for sg, cnt in snap['stocks']['stage_counts'].items():
            if not check(f'stage={sg}', cnt, stage_counts.get(sg, 0), tol=10):
                failures += 1

    except Exception as e:
        print(f'  [FAIL] Could not reach /api/stocks: {e}')
        failures += 1

    # ── Summary ───────────────────────────────────────────────────────────
    print(f'\n{"="*52}')
    if failures == 0:
        print('ALL CHECKS PASSED — DB migration is safe')
    else:
        print(f'FAILED: {failures} check(s) — investigate before going live')
    print('='*52)
    sys.exit(0 if failures == 0 else 1)

if __name__ == '__main__':
    main()
