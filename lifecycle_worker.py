"""
lifecycle_worker.py — Dalal Street Scout
Tracks the phase journey of ALL 2535 NSE stocks from D0 to outcome.

Spec: INSTRUCTIONS_FOR_LIFECYCLE.md
Data: data/lifecycle_log.json  (NEVER touches signals_log.json)
Input: data/computed/stocks.json

Run daily at EOD (same trigger as eval_worker, 3:35 PM Mon–Fri).

Usage:
  python lifecycle_worker.py           # normal EOD run
  python lifecycle_worker.py --init    # wipe and re-init from today
"""

import json
import os
import sys
from datetime import date

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

BASE_DIR        = os.path.dirname(os.path.abspath(__file__))
STOCKS_JSON     = os.path.join(BASE_DIR, 'data', 'computed', 'stocks.json')
LIFECYCLE_JSON  = os.path.join(BASE_DIR, 'data', 'lifecycle_log.json')

WIN_TARGET  = 1.15   # +15%
LOSS_TARGET = 0.91   # -9%
CYCLE_DAYS  = 60

# Phase → canonical index (0 = none, excluded from path display)
PHASE_NUM = {
    'coiling':    1,
    'breakout':   2,
    'pre_cross':  3,
    'post_cross': 4,
    'pullback':   5,
    'trending':   6,
    'none':       0,
}


# ── D0 Snapshot ─────────────────────────────────────────────────────
# Captures all analysis-relevant fields from stocks.json at cycle start.
# Stored once per cycle — never changes mid-cycle.
# Carried into resolved_cycles so past cycles are fully self-contained.

def make_snapshot(stock):
    """Extract a rich D0 snapshot from a stocks.json record."""

    def _f(key, default=None):
        v = stock.get(key, default)
        return v if v is not None else default

    def _sr(key):
        """Support/resistance list — [{price, strength}], capped at 5 levels."""
        levels = stock.get(key) or []
        return [{'price': round(l['price'], 2), 'strength': l.get('strength', 1)}
                for l in levels[:5] if l.get('price')]

    return {
        # ── Technicals ──────────────────────────────────────────────
        'rsi':             _f('rsi'),
        'adx':             _f('adx'),
        'ema14':           _f('ema14'),
        'ema50':           _f('ema50'),
        'ema_gap_pct':     _f('emaGapPct'),       # (ema14-ema50)/price %, negative = below
        'ema14_rising':    _f('ema14Rising'),
        'vol_ratio':       _f('volRatio'),         # today vol / 20d avg vol
        'close_pos':       _f('closePos'),         # (close-low)/(high-low) — candle position
        'avg20_vol':       _f('avg20Vol'),         # 20-day average volume
        'price_coiling':   _f('priceCoiling'),     # range < 4% over 5d
        'vol_shrinking':   _f('volShrinking'),     # vol < 85% avg over 3d

        # ── EMA signals ─────────────────────────────────────────────
        'ema_cross':       _f('emaCross'),
        'ema_cross_days':  _f('emaCrossDays'),
        'ema_pre_cross':   _f('emaPreCross'),
        'ema_post_cross':  _f('emaPostCross'),
        'ema_pullback':    _f('emaPullback'),
        'vol_confirm':     _f('volConfirm'),
        'cross_score':     _f('crossScore', 0),

        # ── VPB signal ──────────────────────────────────────────────
        'vpb_score':       _f('vpbScore', 0),
        'vpb_detail':      _f('vpbDetail', 'none'),

        # ── Scores ──────────────────────────────────────────────────
        'f_score':         _f('fScore', 0),
        't_score':         _f('tScore', 0),
        'score':           _f('score', 0),         # stored total score

        # ── Fundamentals ────────────────────────────────────────────
        'pe':              _f('pe'),
        'debt_eq':         _f('debtEq'),
        'roe':             _f('roe'),
        'promoter':        _f('promoterHolding'),
        'pledging':        _f('pledging'),
        'de_flag':         _f('deFlag'),            # LOW DEBT / MOD / HIGH / NA

        # ── Market context ───────────────────────────────────────────
        'mcap':            _f('mcap'),              # Cr
        'sector':          _f('sector'),
        'sector_median_pe':_f('sectorMedianPe'),
        'pe_vs_sector':    _f('peVsSector'),        # % over/under sector median

        # ── Price context ────────────────────────────────────────────
        'wk52_high':       _f('wk52High'),
        'wk52_low':        _f('wk52Low'),
        'wk38_high':       _f('wk38High'),
        'wk38_low':        _f('wk38Low'),
        'pct_from_38high': _f('pctFrom38High'),    # % below 38W high (negative = below)
        'pct_from_38low':  _f('pctFrom38Low'),     # % above 38W low
        'near_52high':     _f('near52High'),
        'near_38high':     _f('near38High'),
        'upside_pct':      _f('upsidePct'),        # % upside to target
        'target_price':    _f('targetPrice'),
        'target_type':     _f('targetType'),       # "52W" / "ATH9M" / "38W"

        # ── Support & Resistance ─────────────────────────────────────
        # Each level: {price, strength}  (strength = number of touches)
        'sr_resistance':   _sr('srResistance'),    # resistance levels above price
        'sr_support':      _sr('srSupport'),       # support levels below price

        # ── Volume & liquidity ───────────────────────────────────────
        'daily_vol_cr':    _f('dailyVol'),         # daily turnover in Cr
    }


# ── Helpers ─────────────────────────────────────────────────────────

def load_stocks():
    with open(STOCKS_JSON, encoding='utf-8') as f:
        raw = f.read().replace('Infinity', 'null').replace('NaN', 'null')
    data = json.loads(raw)
    return {s['ticker']: s for s in data.get('stocks', [])}


def load_lifecycle():
    if not os.path.exists(LIFECYCLE_JSON):
        return {}
    with open(LIFECYCLE_JSON, encoding='utf-8') as f:
        data = json.load(f)
    return data.get('stocks', {})


def save_lifecycle(records, today_str):
    out = {
        'version':      2,           # bumped: snapshot added
        'last_updated': today_str,
        'stocks':       records,
    }
    with open(LIFECYCLE_JSON, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, separators=(',', ':'))


def build_path(phase_log):
    """Deduped first-visit sequence of named phases (excludes none/0)."""
    seen = []
    for entry in phase_log:
        n = PHASE_NUM.get(entry.get('phase', 'none'), 0)
        if n > 0 and n not in seen:
            seen.append(n)
    return seen


def fmt_path(path):
    return '→'.join(str(n) for n in path) if path else ''


def make_fresh_record(ticker, today_str, stage, price, stock):
    ph_log   = [{'date': today_str, 'phase': stage, 'day_n': 0}]
    p        = build_path(ph_log)
    snapshot = make_snapshot(stock)
    return {
        'ticker':          ticker,
        'd0':              today_str,
        'd0_phase':        stage,
        'd0_price':        price,
        'd0_snapshot':     snapshot,   # ← rich entry-point context
        'phase_log':       ph_log,
        'path':            p,
        'path_str':        fmt_path(p),
        'prices':          {today_str: price},
        'outcome':         'OPEN',
        'outcome_date':    None,
        'outcome_day':     None,
        'outcome_ret':     None,
        'resolved_cycles': [],
    }


def archive_and_reset(rec, outcome, outcome_date_str, outcome_day, outcome_ret,
                      new_d0_str, new_price, new_phase, stock):
    """Save completed cycle to resolved_cycles, then reset record for new cycle."""
    cycle = {
        'd0':           rec['d0'],
        'd0_phase':     rec['d0_phase'],
        'd0_price':     rec['d0_price'],
        'd0_snapshot':  rec.get('d0_snapshot', {}),   # carry full snapshot
        'path':         list(rec.get('path', [])),
        'path_str':     rec.get('path_str', ''),
        'outcome':      outcome,
        'outcome_date': outcome_date_str,
        'outcome_day':  outcome_day,
        'outcome_ret':  outcome_ret,
    }
    rec.setdefault('resolved_cycles', []).append(cycle)

    # Reset for fresh cycle starting at new_d0
    new_snapshot = make_snapshot(stock)
    ph_log = [{'date': new_d0_str, 'phase': new_phase, 'day_n': 0}]
    p      = build_path(ph_log)
    rec.update({
        'd0':           new_d0_str,
        'd0_phase':     new_phase,
        'd0_price':     new_price,
        'd0_snapshot':  new_snapshot,
        'phase_log':    ph_log,
        'path':         p,
        'path_str':     fmt_path(p),
        'prices':       {new_d0_str: new_price},
        'outcome':      'OPEN',
        'outcome_date': None,
        'outcome_day':  None,
        'outcome_ret':  None,
    })


# ── Main run ─────────────────────────────────────────────────────────

def run(force_init=False):
    today     = date.today()
    today_str = today.isoformat()

    print(f'[lifecycle_worker] Starting EOD run for {today_str}')

    stocks  = load_stocks()
    records = {} if force_init else load_lifecycle()

    added   = 0
    updated = 0
    wins    = 0
    losses  = 0
    expired = 0
    skipped = 0

    for ticker, stock in stocks.items():
        price = stock.get('price')
        if not price:
            skipped += 1
            continue
        stage = stock.get('stage', 'none')

        # ── New ticker — initialise with full snapshot ──
        if ticker not in records:
            records[ticker] = make_fresh_record(ticker, today_str, stage, price, stock)
            added += 1
            continue

        rec = records[ticker]

        # ── Already processed today ──
        if today_str in rec.get('prices', {}):
            skipped += 1
            continue

        # ── Backfill snapshot if missing (upgrade from v1) ──
        if 'd0_snapshot' not in rec:
            rec['d0_snapshot'] = make_snapshot(stock)

        d0       = date.fromisoformat(rec['d0'])
        day_n    = (today - d0).days
        d0_price = rec['d0_price']

        # Record today's price
        rec.setdefault('prices', {})[today_str] = price

        # Record phase change (one entry per date, end-of-day phase only)
        phase_log  = rec.setdefault('phase_log', [])
        last_phase = phase_log[-1]['phase'] if phase_log else None
        if stage != last_phase:
            phase_log.append({'date': today_str, 'phase': stage, 'day_n': day_n})

        # Rebuild path from full phase_log
        rec['path']     = build_path(phase_log)
        rec['path_str'] = fmt_path(rec['path'])

        # ── Outcome check (only if still OPEN) ──
        if rec.get('outcome', 'OPEN') == 'OPEN':
            ret = round((price - d0_price) / d0_price * 100, 2) if d0_price else 0.0

            if price >= d0_price * WIN_TARGET:
                archive_and_reset(rec, 'WIN', today_str, day_n, ret,
                                  today_str, price, stage, stock)
                wins += 1

            elif price <= d0_price * LOSS_TARGET:
                archive_and_reset(rec, 'LOSS', today_str, day_n, ret,
                                  today_str, price, stage, stock)
                losses += 1

            elif day_n >= CYCLE_DAYS:
                archive_and_reset(rec, 'EXPIRED', today_str, day_n, ret,
                                  today_str, price, stage, stock)
                expired += 1

        updated += 1

    save_lifecycle(records, today_str)

    total = len(records)
    print(f'[lifecycle_worker] Done — {total} stocks tracked')
    print(f'  New: {added}  Updated: {updated}  Skipped: {skipped}')
    print(f'  Resolved today — WIN: {wins}  LOSS: {losses}  EXPIRED: {expired}')


if __name__ == '__main__':
    force_init = '--init' in sys.argv
    if force_init:
        print('[lifecycle_worker] --init flag: wiping and re-initialising with full snapshots')
    run(force_init=force_init)
