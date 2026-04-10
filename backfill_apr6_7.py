"""
backfill_apr6_7.py — Backfill April 6 and 7 closing prices into signals_log.json.

The EOD pipeline failed on April 6 and 7 because REFRESH_EOD.py didn't exist.
This script recovers price data for existing open signals using the fresh kite.json
OHLCV history (which has complete closing candles for both missed dates).

Usage:
    python backfill_apr6_7.py [--dry-run]

What it does:
    - Extracts April 6 + April 7 closing prices per ticker from kite.json
    - Injects those prices into `prices{}` for all open signals missing them
    - Re-resolves WIN/LOSS/EXPIRED outcomes using updated price history
    - Saves signals_log.json

What it does NOT do:
    - Log new signals for those dates (requires stocks.json from those days — gone)
    - Record stage transitions (same reason)
    - Build full daily_snapshots (technical fields unavailable)
"""

import sys
import os
import json
import datetime
import argparse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
KITE_FILE    = os.path.join(BASE_DIR, 'data', 'raw', 'kite.json')
SIGNALS_FILE = os.path.join(BASE_DIR, 'data', 'signals_log.json')

BACKFILL_DATES = ['2026-04-06', '2026-04-07']

WIN_TARGET  = 1.15
LOSS_TARGET = 0.91
MAX_CALENDAR_DAYS = 60


def load_kite_closes():
    """Return dict: {ticker: {date_str: close_price}} for BACKFILL_DATES."""
    print(f'[BACKFILL] Loading kite.json ...')
    with open(KITE_FILE, encoding='utf-8') as f:
        kite = json.load(f)

    ohlcv = kite.get('ohlcv', {})
    prices = {}  # ticker → {date: close}

    # Verify the dates are present
    date_set = set(BACKFILL_DATES)
    found_dates = set()

    for ticker, entry in ohlcv.items():
        rows = entry.get('rows', []) if isinstance(entry, dict) else []
        ticker_prices = {}
        for row in rows:
            # row = [date, open, high, low, close, volume]
            if len(row) >= 5 and row[0] in date_set:
                close = float(row[4])
                if close > 0:
                    ticker_prices[row[0]] = close
                    found_dates.add(row[0])
        if ticker_prices:
            prices[ticker] = ticker_prices

    print(f'[BACKFILL] Kite saved_at: {kite.get("saved_at", "unknown")}')
    print(f'[BACKFILL] Dates found in kite.json: {sorted(found_dates)}')
    print(f'[BACKFILL] Tickers with backfill data: {len(prices)}')
    return prices


def load_signals():
    with open(SIGNALS_FILE, encoding='utf-8') as f:
        data = json.load(f)
    return data.get('signals', []), data


def save_signals(data, signals):
    data['signals'] = signals
    with open(SIGNALS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f)
    print(f'[BACKFILL] Saved {len(signals)} signals -> {SIGNALS_FILE}')


def compute_outcome(sig):
    """Re-resolve WIN/LOSS/EXPIRED from prices dict. Clears existing outcome first."""
    prices_dict = sig.get('prices', {})
    p0  = sig.get('price_d0', 0)
    day0 = sig.get('day0', '')
    if not prices_dict or not p0 or not day0:
        return

    try:
        day0_date   = datetime.date.fromisoformat(day0)
        expiry_date = day0_date + datetime.timedelta(days=MAX_CALENDAR_DAYS)
    except ValueError:
        return

    sorted_dates = sorted(prices_dict.keys())

    # Reset outcome fields before re-computing
    sig['outcome']       = 'OPEN'
    sig['outcome_day']   = None
    sig.pop('outcome_date', None)
    sig['outcome_price'] = None
    sig['outcome_ret']   = None

    for day_idx, date_str in enumerate(sorted_dates):
        price = prices_dict.get(date_str, 0)
        if not price:
            continue
        # Loss first (matches eval_worker logic)
        if price <= p0 * LOSS_TARGET:
            sig['outcome']       = 'LOSS'
            sig['outcome_day']   = day_idx
            sig['outcome_date']  = date_str
            sig['outcome_price'] = price
            sig['outcome_ret']   = round((price - p0) / p0 * 100, 2)
            return
        if price >= p0 * WIN_TARGET:
            sig['outcome']       = 'WIN'
            sig['outcome_day']   = day_idx
            sig['outcome_date']  = date_str
            sig['outcome_price'] = price
            sig['outcome_ret']   = round((price - p0) / p0 * 100, 2)
            return
        try:
            current_date = datetime.date.fromisoformat(date_str)
        except ValueError:
            continue
        if current_date >= expiry_date:
            sig['outcome']       = 'EXPIRED'
            sig['outcome_day']   = day_idx
            sig['outcome_date']  = date_str
            sig['outcome_price'] = price
            sig['outcome_ret']   = round((price - p0) / p0 * 100, 2)
            return


def is_within_tracking_window(sig):
    if sig.get('outcome') in ('WIN', 'LOSS', 'EXPIRED'):
        return False
    day0 = sig.get('day0', '')
    if not day0:
        return False
    try:
        age = (datetime.date.today() - datetime.date.fromisoformat(day0)).days
        return age <= (MAX_CALENDAR_DAYS + 5)
    except Exception:
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true', help='Show stats without saving')
    args = parser.parse_args()

    kite_closes = load_kite_closes()

    missing_dates = [d for d in BACKFILL_DATES if not any(d in v for v in kite_closes.values())]
    if missing_dates:
        print(f'[BACKFILL] WARNING: These dates are missing from kite.json: {missing_dates}')
        print('[BACKFILL] Will backfill only the dates that are available.')
    if not kite_closes:
        print('[BACKFILL] No data found in kite.json — aborting.')
        sys.exit(1)

    signals, raw_data = load_signals()
    print(f'[BACKFILL] Loaded {len(signals)} signals')

    injected   = {d: 0 for d in BACKFILL_DATES}
    skipped_resolved = 0
    skipped_window   = 0
    skipped_no_data  = 0
    outcome_changes  = {'WIN': 0, 'LOSS': 0, 'EXPIRED': 0}

    for sig in signals:
        ticker = sig.get('ticker', '')

        # Skip already-resolved (we re-resolve below after injecting)
        was_resolved = sig.get('outcome') in ('WIN', 'LOSS', 'EXPIRED')
        if was_resolved:
            skipped_resolved += 1
            continue

        if not is_within_tracking_window(sig):
            skipped_window += 1
            continue

        ticker_data = kite_closes.get(ticker, {})
        if not ticker_data:
            skipped_no_data += 1
            continue

        prices = sig.setdefault('prices', {})
        changed = False
        for date_str in BACKFILL_DATES:
            if date_str not in prices and date_str in ticker_data:
                prices[date_str] = ticker_data[date_str]
                injected[date_str] += 1
                changed = True

        if changed:
            prev_outcome = sig.get('outcome', 'OPEN')
            compute_outcome(sig)
            new_outcome = sig.get('outcome', 'OPEN')
            if new_outcome != 'OPEN' and prev_outcome == 'OPEN':
                outcome_changes[new_outcome] += 1

    print(f'\n[BACKFILL] Results:')
    for date_str in BACKFILL_DATES:
        print(f'  {date_str}: {injected[date_str]} prices injected')
    print(f'  Skipped (already resolved): {skipped_resolved}')
    print(f'  Skipped (outside window):   {skipped_window}')
    print(f'  Skipped (no kite data):     {skipped_no_data}')
    print(f'  New WIN outcomes:           {outcome_changes["WIN"]}')
    print(f'  New LOSS outcomes:          {outcome_changes["LOSS"]}')
    print(f'  New EXPIRED outcomes:       {outcome_changes["EXPIRED"]}')

    if args.dry_run:
        print('\n[BACKFILL] Dry run — not saving.')
        return

    save_signals(raw_data, signals)
    print('[BACKFILL] Done.')


if __name__ == '__main__':
    main()
