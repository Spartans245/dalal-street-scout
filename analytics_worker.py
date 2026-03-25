"""
analytics_worker.py — EVALS Analytics Aggregator
==================================================
Reads signals_log.json, computes pre-aggregated analytics metrics,
writes results to data/evals/analytics.json.

Run after eval_worker --eod (called by orchestrator).

Reads (never writes):
  data/signals_log.json

Writes:
  data/evals/analytics.json
"""

import sys, os, json, datetime, statistics

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

BASE_DIR       = os.path.dirname(os.path.abspath(__file__))
SIGNALS_FILE   = os.path.join(BASE_DIR, 'data', 'signals_log.json')
ANALYTICS_FILE = os.path.join(BASE_DIR, 'data', 'evals', 'analytics.json')

CHECKPOINTS = [7, 14, 21, 30, 45, 60]
ALL_STAGES  = ['coiling', 'breakout', 'pre_cross', 'post_cross', 'pullback', 'trending']


# ── Helpers ──────────────────────────────────────────────────────────────────

def load_signals():
    try:
        with open(SIGNALS_FILE, encoding='utf-8') as f:
            data = json.load(f)
        return data.get('signals', []) if isinstance(data, dict) else data
    except Exception as e:
        print(f'[ANALYTICS] Cannot load signals: {e}')
        return []


def stock_ret_at_day(sig, day_idx):
    """% return vs price_d0 at trading day index day_idx. None if no data."""
    p0 = sig.get('price_d0', 0)
    if not p0:
        return None
    sorted_dates = sorted(sig.get('prices', {}).keys())
    if len(sorted_dates) <= day_idx:
        return None
    price = sig['prices'].get(sorted_dates[day_idx], 0)
    if not price:
        return None
    return round((price - p0) / p0 * 100, 2)


def nifty_ret_at_day(sig, day_idx):
    """
    NIFTY % return at trading day index day_idx, measured from this signal's
    own nifty_d0 (NIFTY price on the signal's entry date).
    Uses the signal's nifty_prices dict — same day indices as prices dict.
    """
    n0 = sig.get('nifty_d0', 0)
    if not n0:
        return None
    # Use stock price dates as the day index axis (same trading days)
    sorted_dates = sorted(sig.get('prices', {}).keys())
    if len(sorted_dates) <= day_idx:
        return None
    date_str = sorted_dates[day_idx]
    nifty_prices = sig.get('nifty_prices', {})
    np_price = nifty_prices.get(date_str, 0)
    if not np_price:
        return None
    return round((np_price - n0) / n0 * 100, 2)


def _med(arr):
    return round(statistics.median(arr), 2) if arr else None

def _mean(arr):
    return round(statistics.mean(arr), 2) if arr else None


def checkpoint_stats(sigs):
    """
    For each checkpoint, compute stock and NIFTY median/mean/n.
    Returns dict keyed by 'd7', 'd14', etc.
    Each value: {stock_median, stock_mean, nifty_median, nifty_mean, alpha_median, n, n_nifty}
    """
    result = {}
    for cp in CHECKPOINTS:
        stock_rets = [r for s in sigs for r in [stock_ret_at_day(s, cp)] if r is not None]
        nifty_rets = [r for s in sigs for r in [nifty_ret_at_day(s, cp)] if r is not None]

        # Alpha per signal: stock_ret - nifty_ret (only where both available)
        alpha_rets = []
        for s in sigs:
            sr = stock_ret_at_day(s, cp)
            nr = nifty_ret_at_day(s, cp)
            if sr is not None and nr is not None:
                alpha_rets.append(round(sr - nr, 2))

        result[f'd{cp}'] = {
            'stock_median':  _med(stock_rets),
            'stock_mean':    _mean(stock_rets),
            'nifty_median':  _med(nifty_rets),
            'nifty_mean':    _mean(nifty_rets),
            'alpha_median':  _med(alpha_rets),
            'n':             len(stock_rets),
            'n_nifty':       len(nifty_rets),
        }
    return result


# ── Main computation ──────────────────────────────────────────────────────────

def distribution_stats(sigs):
    """
    For each checkpoint, compute total, wins (≥+15%), best and worst return.
    Returns dict keyed by 'd7', 'd14', etc.
    Each value: {total, wins, best, worst}
    """
    result = {}
    for cp in CHECKPOINTS:
        rets = [r for s in sigs for r in [stock_ret_at_day(s, cp)] if r is not None]
        wins = len([r for r in rets if r >= 15.0])
        losses = len([r for r in rets if r <= -9.0])
        result[f'd{cp}'] = {
            'total':  len(rets),
            'wins':   wins,
            'losses': losses,
            'best':   round(max(rets), 2) if rets else None,
            'worst':  round(min(rets), 2) if rets else None,
        }
    return result


def sig_path(sig):
    """Full forward path: entry stage + unique stage transitions from stage_history."""
    hist = sorted(sig.get('stage_history', []), key=lambda h: h.get('day_n', 0) if isinstance(h, dict) else 0)
    stages = [sig.get('stage', '')]
    for h in hist:
        s = h.get('stage', h) if isinstance(h, dict) else h
        if s and s != stages[-1]:
            stages.append(s)
    return stages


def follows_sequence(stages):
    """True if stage progression is non-decreasing in canonical order."""
    last_idx = -1
    for s in stages:
        try:
            idx = ALL_STAGES.index(s)
        except ValueError:
            continue
        if idx < last_idx:
            return False
        last_idx = idx
    return True


RESOLVED = ('WIN', 'LOSS', 'EXPIRED')   # outcomes that are no longer tracked

def _win_rate(sigs):
    # WIN rate = wins / (wins + losses). EXPIRED signals excluded (no clear win/loss).
    closed = [s for s in sigs if s.get('outcome') in ('WIN', 'LOSS')]
    if not closed:
        return None
    return round(len([s for s in sigs if s.get('outcome') == 'WIN']) / len(closed) * 100)


def _avg_ret(sigs):
    w = [s for s in sigs if s.get('outcome_ret') is not None]
    return round(sum(s['outcome_ret'] for s in w) / len(w), 1) if w else None


def _median_day(sigs):
    days = sorted([s['outcome_day'] for s in sigs if s.get('outcome_day') is not None])
    return days[len(days) // 2] if days else None


def compute_lifecycle(signals):
    """
    Compute lifecycle analytics:
    - sequence_adherence: follows vs skipped summary + per-stage progression rates
    - path_map: all unique forward paths with frequency, win%, avg_ret, median_day
    - days_to_outcome: median outcome_day by entry stage for WIN and LOSS
    """
    # Attach full path to each signal
    sigs_with_path = [(s, sig_path(s)) for s in signals]

    # Sequence adherence
    follows = [(s, p) for s, p in sigs_with_path if follows_sequence(p)]
    skipped = [(s, p) for s, p in sigs_with_path if not follows_sequence(p)]

    follows_sigs = [s for s, _ in follows]
    skipped_sigs = [s for s, _ in skipped]

    # Stage progression rates
    progression = []
    for i, from_stage in enumerate(ALL_STAGES[:-1]):
        to_stage = ALL_STAGES[i + 1]
        visited_from = [(s, p) for s, p in sigs_with_path if from_stage in p]
        visited_both = [(s, p) for s, p in visited_from if to_stage in p[p.index(from_stage) + 1:]]
        vf_sigs = [s for s, _ in visited_from]
        vb_sigs = [s for s, _ in visited_both]
        pct = round(len(vb_sigs) / len(vf_sigs) * 100) if vf_sigs else 0
        progression.append({
            'from': from_stage, 'to': to_stage,
            'n_from': len(vf_sigs), 'n_both': len(vb_sigs),
            'pct': pct,
            'win_rate': _win_rate(vb_sigs),
        })

    # Path map
    path_counts = {}
    for s, p in sigs_with_path:
        path_str = ' → '.join(p)
        outcome  = s.get('outcome', 'OPEN') or 'OPEN'
        key      = path_str + ' → ' + outcome
        if key not in path_counts:
            path_counts[key] = {'path': key, 'sigs': []}
        path_counts[key]['sigs'].append(s)

    path_map = []
    for info in sorted(path_counts.values(), key=lambda x: -len(x['sigs'])):
        ps = info['sigs']
        path_map.append({
            'path':     info['path'],
            'n':        len(ps),
            'win_rate': _win_rate(ps),
            'avg_ret':  _avg_ret(ps),
            'med_day':  _median_day(ps),
        })

    # Days to outcome by entry stage
    days_to_outcome = {}
    for stage in ALL_STAGES:
        ss = [s for s in signals if s.get('stage') == stage]
        if not ss:
            continue
        wins    = [s for s in ss if s.get('outcome') == 'WIN']
        losses  = [s for s in ss if s.get('outcome') == 'LOSS']
        expired = [s for s in ss if s.get('outcome') == 'EXPIRED']
        open_   = [s for s in ss if not s.get('outcome') or s.get('outcome') == 'OPEN']
        days_to_outcome[stage] = {
            'total':       len(ss),
            'win_n':       len(wins),    'win_med_day':  _median_day(wins),
            'loss_n':      len(losses),  'loss_med_day': _median_day(losses),
            'expired_n':   len(expired),
            'open_n':      len(open_),
        }

    return {
        'sequence_adherence': {
            'follows_n':  len(follows_sigs),
            'follows_wr': _win_rate(follows_sigs),
            'follows_ar': _avg_ret(follows_sigs),
            'skipped_n':  len(skipped_sigs),
            'skipped_wr': _win_rate(skipped_sigs),
            'skipped_ar': _avg_ret(skipped_sigs),
            'progression': progression,
        },
        'path_map':       path_map,
        'days_to_outcome': days_to_outcome,
    }


def compute_analytics(signals):
    return_by_stage     = {}
    distribution_by_stage = {}

    for stage in ALL_STAGES:
        sigs = [s for s in signals if s.get('stage') == stage]
        return_by_stage[stage]      = checkpoint_stats(sigs)
        distribution_by_stage[stage] = distribution_stats(sigs)

    return_by_stage['_all']       = checkpoint_stats(signals)
    distribution_by_stage['_all'] = distribution_stats(signals)

    return {
        'generated_at':          datetime.date.today().isoformat(),
        'total_signals':         len(signals),
        'return_by_stage':       return_by_stage,
        'distribution_by_stage': distribution_by_stage,
        'lifecycle':             compute_lifecycle(signals),
    }


def save_analytics(data):
    tmp = ANALYTICS_FILE + '.tmp'
    os.makedirs(os.path.dirname(ANALYTICS_FILE), exist_ok=True)
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, ANALYTICS_FILE)


def run():
    print('[ANALYTICS] Computing analytics from signals_log.json...')
    signals = load_signals()
    if not signals:
        print('[ANALYTICS] No signals found — writing empty analytics')
        save_analytics({'generated_at': datetime.date.today().isoformat(),
                        'total_signals': 0, 'return_by_stage': {}})
        return

    data = compute_analytics(signals)
    save_analytics(data)

    rbs = data['return_by_stage']
    for stage in ALL_STAGES:
        d30 = rbs.get(stage, {}).get('d30', {})
        print(f"[ANALYTICS] {stage:12s} D30 stock={d30.get('stock_median')}% "
              f"nifty={d30.get('nifty_median')}% alpha={d30.get('alpha_median')}% (n={d30.get('n',0)})")

    lc = data.get('lifecycle', {})
    sa = lc.get('sequence_adherence', {})
    print(f"[ANALYTICS] Lifecycle: follows={sa.get('follows_n',0)} "
          f"skipped={sa.get('skipped_n',0)} paths={len(lc.get('path_map',[]))}")
    print(f'[ANALYTICS] Done — {len(signals)} signals processed → {ANALYTICS_FILE}')


if __name__ == '__main__':
    run()
