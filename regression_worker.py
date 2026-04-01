"""
regression_worker.py — Feature Attribution Model for EVALS

Reads data/signals_log.json (resolved signals only).
Computes win-rate lift + Information Value for every parameter,
then writes data/evals/regression.json.

No external ML libraries — pure Python math.

Run:
    python model_worker.py            # compute and save
    python model_worker.py --dry-run  # print summary, don't save

Information Value (IV) interpretation:
    < 0.02  → Useless predictor
    0.02–0.1 → Weak
    0.1–0.3  → Medium
    > 0.3    → Strong
"""

import sys, os, json, math, datetime, argparse
from collections import defaultdict

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
SIGNALS_FILE = os.path.join(BASE_DIR, 'data', 'signals_log.json')
REGRESSION_FILE   = os.path.join(BASE_DIR, 'data', 'evals', 'regression.json')
MIN_RESOLVED = 5   # minimum closed signals before model runs

# ─── helpers ────────────────────────────────────────────────────────────────

def load_signals():
    with open(SIGNALS_FILE, encoding='utf-8') as f:
        return json.load(f)['signals']

def snap0(sig):
    """Return the D0 daily snapshot dict (richest source of features)."""
    snaps = sig.get('daily_snapshots') or {}
    return snaps.get(sig.get('tier_date') or sig.get('day0')) or {}

def get(sig, key, fallback=None):
    """Get a field: try D0 snapshot first, then signal entry, then fallback."""
    s = snap0(sig)
    if key in s and s[key] is not None:
        return s[key]
    v = sig.get(key)
    return v if v is not None else fallback

# ─── bucketing helpers ───────────────────────────────────────────────────────

def bkt_rsi(v):
    if v is None: return None
    if v < 40:    return '<40'
    if v < 45:    return '40–45'
    if v < 50:    return '45–50'
    if v < 55:    return '50–55'
    if v < 58:    return '55–58'
    if v < 65:    return '58–65'
    return '>65'

def bkt_adx(v):
    if v is None: return None
    if v < 15:    return '<15'
    if v < 20:    return '15–20'
    if v < 25:    return '20–25'
    if v < 30:    return '25–30'
    if v < 35:    return '30–35'
    return '>35'

def bkt_fscore(v):
    if v is None: return None
    if v == 0:    return '0'
    if v <= 8:    return '1–8'
    if v <= 15:   return '9–15'
    if v <= 20:   return '16–20'
    if v <= 25:   return '21–25'
    return '>25'

def bkt_tscore(v):
    if v is None: return None
    if v <= 0:    return '0'
    if v <= 10:   return '1–10'
    if v <= 15:   return '11–15'
    if v <= 18:   return '16–18'
    return '19–22'

def bkt_total(v):
    if v is None: return None
    if v < 20:    return '<20'
    if v < 34:    return '20–33'
    if v < 45:    return '34–44'
    if v < 57:    return '45–56'
    return '≥57'

def bkt_vpb(v):
    if v is None: return None
    if v == 0:    return '0'
    if v <= 3:    return '1–3 (COIL)'
    if v <= 6:    return '4–6 (BRK~)'
    if v <= 8:    return '7–8 (BRK✓)'
    return '9–10 (BRK✓✓)'

def bkt_cross(v):
    if v is None: return None
    if v == 0:    return '0 (no cross)'
    if v <= 8:    return '8 (old/no-vol)'
    if v <= 10:   return '10 (5d+vol)'
    if v <= 14:   return '14 (3-4d+vol)'
    return '18 (1-2d+vol)'

def bkt_upside(v):
    if v is None: return None
    if v < 5:     return '<5%'
    if v < 10:    return '5–10%'
    if v < 20:    return '10–20%'
    if v < 40:    return '20–40%'
    return '>40%'

def bkt_mcap(v):
    if v is None: return None
    if v < 500:   return '<500Cr (SC)'
    if v < 2000:  return '500–2k Cr (MC)'
    if v < 10000: return '2k–10k Cr (LC)'
    return '>10k Cr (LL)'

def bkt_pe(v):
    if v is None or v <= 0: return 'N/A'
    if v < 15:    return '<15'
    if v < 25:    return '15–25'
    if v < 40:    return '25–40'
    return '>40'

def bkt_de(v):
    if v is None or v < 0: return 'N/A'
    if v == 0:    return '0 (Debt Free)'
    if v < 0.5:   return '<0.5 (Low)'
    if v < 1:     return '0.5–1'
    if v < 2:     return '1–2'
    return '>2 (High)'

def bkt_volratio(v):
    if v is None: return None
    if v < 0.5:   return '<0.5× (Dry)'
    if v < 1.0:   return '0.5–1× (Quiet)'
    if v < 1.5:   return '1–1.5× (Normal)'
    if v < 2.0:   return '1.5–2× (Active)'
    return '>2× (Surge)'

def bkt_closepos(v):
    if v is None: return None
    if v < 0.2:   return '<20% (Bearish)'
    if v < 0.4:   return '20–40%'
    if v < 0.6:   return '40–60% (Neutral)'
    if v < 0.8:   return '60–80%'
    return '>80% (Bullish)'

def bkt_ema_gap(v):
    if v is None: return None
    if v < -10:   return '<-10% (Far below)'
    if v < -5:    return '-10 to -5%'
    if v < -2:    return '-5 to -2%'
    if v < 0:     return '-2 to 0%'
    if v < 2:     return '0 to +2%'
    return '>+2% (Above)'

def bkt_from52(v):
    if v is None: return None
    if v < -40:   return '<-40%'
    if v < -25:   return '-40 to -25%'
    if v < -15:   return '-25 to -15%'
    if v < -5:    return '-15 to -5%'
    return '>-5% (Near ATH)'

def bkt_promoter(v):
    if v is None: return None
    if v < 30:    return '<30%'
    if v < 50:    return '30–50%'
    if v < 70:    return '50–70%'
    return '>70%'

def bkt_pe_vs_sector(v):
    if v is None: return None
    if v < -20:   return 'Deep discount >-20%'
    if v < -5:    return 'Discount -5 to -20%'
    if v < 5:     return 'In line ±5%'
    if v < 20:    return 'Premium 5–20%'
    return 'High premium >20%'

def bool_label(v):
    if v is None: return None
    return 'Yes' if v else 'No'

# ─── feature definitions ─────────────────────────────────────────────────────
# Each entry: (display_name, type, getter_fn)
# type: 'cat' | 'ord' | 'bool' | 'combo'
# getter_fn: takes signal dict, returns bucket string or None

FEATURE_DEFS = [
    # Entry stage & signal type
    ('Entry Stage',        'cat',  lambda s: get(s,'stage')),
    ('VPB Detail',         'cat',  lambda s: get(s,'vpbDetail') or 'none'),
    # Scores
    ('F Score (Fundamentals)', 'ord', lambda s: bkt_fscore(get(s,'fScore'))),
    ('T Score (Technicals)',   'ord', lambda s: bkt_tscore((get(s,'tScore') or 0) if get(s,'tScore') is not None
                                                else (get(s,'rsiScore',0) or 0) + (get(s,'adxScore',0) or 0))),
    ('Signal Score',       'ord',  lambda s: bkt_cross(get(s,'crossScore')) if (get(s,'crossScore') or 0) > 0
                                             else bkt_vpb(get(s,'vpbScore'))),
    ('Total Score',        'ord',  lambda s: bkt_total(get(s,'score'))),
    # Technicals
    ('RSI',                'ord',  lambda s: bkt_rsi(get(s,'rsi'))),
    ('ADX',                'ord',  lambda s: bkt_adx(get(s,'adx'))),
    ('EMA Gap %',          'ord',  lambda s: bkt_ema_gap(get(s,'emaGapPct'))),
    ('VPB Score',          'ord',  lambda s: bkt_vpb(get(s,'vpbScore'))),
    ('Cross Score',        'ord',  lambda s: bkt_cross(get(s,'crossScore'))),
    ('Volume Ratio (D0)',  'ord',  lambda s: bkt_volratio(get(s,'volRatio'))),
    ('Close Position (D0)','ord',  lambda s: bkt_closepos(get(s,'closePos'))),
    ('% from 52W High',   'ord',  lambda s: bkt_from52(get(s,'pctFrom52High'))),
    # EMA boolean signals
    ('EMA Cross',          'bool', lambda s: bool_label(get(s,'emaCross'))),
    ('Vol-Confirmed Cross','bool', lambda s: bool_label(get(s,'volConfirm'))),
    ('Pre-Cross',          'bool', lambda s: bool_label(get(s,'emaPreCross'))),
    ('Pullback',           'bool', lambda s: bool_label(get(s,'emaPullback'))),
    ('EMA14 Rising Fast',  'bool', lambda s: bool_label(get(s,'ema14RisingFast'))),
    # VPB boolean signals
    ('Price Coiling',      'bool', lambda s: bool_label(get(s,'priceCoiling'))),
    ('Vol Shrinking',      'bool', lambda s: bool_label(get(s,'volShrinking'))),
    ('Near 52W High',      'bool', lambda s: bool_label(get(s,'near52High'))),
    ('MACD Bullish',       'bool', lambda s: bool_label(get(s,'macd'))),
    # Fundamentals
    ('P/E Ratio',          'ord',  lambda s: bkt_pe(get(s,'pe'))),
    ('D/E Ratio',          'ord',  lambda s: bkt_de(get(s,'debtEq') or get(s,'de'))),
    ('P/E vs Sector',      'ord',  lambda s: bkt_pe_vs_sector(get(s,'peVsSector'))),
    ('Market Cap',         'ord',  lambda s: bkt_mcap(get(s,'mcap'))),
    ('Promoter Holding %', 'ord',  lambda s: bkt_promoter(get(s,'promoterHolding'))),
    # Context
    ('Max Upside %',       'ord',  lambda s: bkt_upside(get(s,'upsidePct'))),
    ('Sector',             'cat',  lambda s: get(s,'sector') or 'Unknown'),
    # Combination features (derived)
    ('Setup: Full VPB (Coil+Vol)',  'combo', lambda s: bool_label(get(s,'priceCoiling') and get(s,'volShrinking'))),
    ('Setup: Cross+Vol Confirm',    'combo', lambda s: bool_label(get(s,'emaCross') and get(s,'volConfirm'))),
    ('Setup: Strong F+T (≥40)',     'combo', lambda s: 'Yes' if (get(s,'fScore') or 0) + ((get(s,'tScore') or 0)) >= 40 else 'No'),
    ('Setup: Ideal RSI+ADX',        'combo', lambda s: bool_label(
        45 <= (get(s,'rsi') or 0) <= 58 and (get(s,'adx') or 0) >= 20)),
    ('Setup: High F + Ideal RSI',   'combo', lambda s: bool_label(
        (get(s,'fScore') or 0) >= 16 and 45 <= (get(s,'rsi') or 0) <= 58)),
    ('Setup: BRK✓ + Good ADX',      'combo', lambda s: bool_label(
        (get(s,'vpbScore') or 0) >= 7 and (get(s,'adx') or 0) >= 20)),
    ('Setup: Cross + Pre-Cross',    'combo', lambda s: bool_label(
        get(s,'emaCross') or get(s,'emaPreCross'))),
]

# ─── information value ───────────────────────────────────────────────────────

def compute_iv(buckets, total_win, total_loss):
    """
    IV = Σ (%WINs_in_bucket - %LOSSes_in_bucket) × ln(%WINs / %LOSSes)
    Skip buckets with 0 wins or 0 losses (log undefined).
    """
    iv = 0.0
    for b in buckets:
        pct_w = b['win'] / total_win   if total_win  > 0 else 0
        pct_l = b['loss'] / total_loss if total_loss > 0 else 0
        if pct_w > 0 and pct_l > 0:
            iv += (pct_w - pct_l) * math.log(pct_w / pct_l)
    return round(iv, 4)

# ─── core computation ────────────────────────────────────────────────────────

def compute_feature(signals, name, type_, getter, baseline_wr, total_win, total_loss):
    groups = defaultdict(lambda: {'win': 0, 'loss': 0})
    for s in signals:
        val = getter(s)
        if val is None:
            continue
        outcome = s.get('outcome')
        if outcome == 'WIN':
            groups[val]['win'] += 1
        elif outcome == 'LOSS':
            groups[val]['loss'] += 1

    buckets = []
    for label, c in groups.items():
        n = c['win'] + c['loss']
        wr = c['win'] / n if n > 0 else 0
        buckets.append({
            'label':    label,
            'n':        n,
            'win':      c['win'],
            'loss':     c['loss'],
            'win_rate': round(wr, 3),
            'lift':     round(wr / baseline_wr, 2) if baseline_wr > 0 else 1.0,
        })

    buckets.sort(key=lambda b: -b['win_rate'])
    iv = compute_iv(buckets, total_win, total_loss)

    return {
        'name':    name,
        'type':    type_,
        'iv':      iv,
        'buckets': buckets,
    }


def compute_boolean_combos(signals, baseline_wr):
    """All 2-way and 3-way combinations of boolean/combo features."""
    BOOL_KEYS = [
        ('emaCross',       lambda s: bool(get(s,'emaCross'))),
        ('volConfirm',     lambda s: bool(get(s,'volConfirm'))),
        ('emaPreCross',    lambda s: bool(get(s,'emaPreCross'))),
        ('emaPullback',    lambda s: bool(get(s,'emaPullback'))),
        ('priceCoiling',   lambda s: bool(get(s,'priceCoiling'))),
        ('volShrinking',   lambda s: bool(get(s,'volShrinking'))),
        ('ema14RisingFast',lambda s: bool(get(s,'ema14RisingFast'))),
        ('near52High',     lambda s: bool(get(s,'near52High'))),
        ('macd',           lambda s: bool(get(s,'macd'))),
        ('highF',          lambda s: (get(s,'fScore') or 0) >= 16),
        ('idealRSI',       lambda s: 45 <= (get(s,'rsi') or 0) <= 58),
        ('strongADX',      lambda s: (get(s,'adx') or 0) >= 20),
        ('highVPB',        lambda s: (get(s,'vpbScore') or 0) >= 7),
    ]

    results = []

    def score_combo(flags_names, flags_fns):
        matched = [s for s in signals if all(fn(s) for fn in flags_fns)
                   and s.get('outcome') in ('WIN','LOSS')]
        n = len(matched)
        if n < 3:
            return None
        wins = sum(1 for s in matched if s['outcome'] == 'WIN')
        wr = wins / n
        return {
            'flags':    flags_names,
            'n':        n,
            'win':      wins,
            'loss':     n - wins,
            'win_rate': round(wr, 3),
            'lift':     round(wr / baseline_wr, 2) if baseline_wr > 0 else 1.0,
        }

    # 2-way combos
    for i in range(len(BOOL_KEYS)):
        for j in range(i+1, len(BOOL_KEYS)):
            names = [BOOL_KEYS[i][0], BOOL_KEYS[j][0]]
            fns   = [BOOL_KEYS[i][1], BOOL_KEYS[j][1]]
            r = score_combo(names, fns)
            if r:
                results.append(r)

    # 3-way combos
    for i in range(len(BOOL_KEYS)):
        for j in range(i+1, len(BOOL_KEYS)):
            for k in range(j+1, len(BOOL_KEYS)):
                names = [BOOL_KEYS[i][0], BOOL_KEYS[j][0], BOOL_KEYS[k][0]]
                fns   = [BOOL_KEYS[i][1], BOOL_KEYS[j][1], BOOL_KEYS[k][1]]
                r = score_combo(names, fns)
                if r:
                    results.append(r)

    results.sort(key=lambda r: (-r['win_rate'], -r['n']))
    return results[:50]  # top-50 combos


def compute_rsi_adx_heatmap(signals, baseline_wr):
    """4×5 cross-tab: RSI bands × ADX bands."""
    RSI_BANDS = [('<45', lambda v: v < 45),
                 ('45–50', lambda v: 45 <= v < 50),
                 ('50–55', lambda v: 50 <= v < 55),
                 ('55–58', lambda v: 55 <= v < 58),
                 ('58–65', lambda v: 58 <= v < 65),
                 ('>65',  lambda v: v >= 65)]
    ADX_BANDS = [('<15',   lambda v: v < 15),
                 ('15–20', lambda v: 15 <= v < 20),
                 ('20–25', lambda v: 20 <= v < 25),
                 ('25–30', lambda v: 25 <= v < 30),
                 ('30–35', lambda v: 30 <= v < 35),
                 ('>35',   lambda v: v >= 35)]

    cells = []
    for adx_label, adx_fn in ADX_BANDS:
        row = []
        for rsi_label, rsi_fn in RSI_BANDS:
            matched = []
            for s in signals:
                if s.get('outcome') not in ('WIN', 'LOSS'):
                    continue
                rsi = get(s, 'rsi')
                adx = get(s, 'adx')
                if rsi is None or adx is None:
                    continue
                if rsi_fn(rsi) and adx_fn(adx):
                    matched.append(s)
            n    = len(matched)
            wins = sum(1 for s in matched if s['outcome'] == 'WIN')
            wr   = wins / n if n > 0 else None
            row.append({
                'rsi': rsi_label,
                'adx': adx_label,
                'n':   n,
                'win': wins,
                'win_rate': round(wr, 3) if wr is not None else None,
                'lift': round(wr / baseline_wr, 2) if (wr is not None and baseline_wr > 0) else None,
            })
        cells.append({'adx_band': adx_label, 'cells': row})

    return cells


def compute_score_bands(signals, baseline_wr):
    BANDS = [
        ('≥57  (Strong ALL)', lambda s: (get(s,'score') or 0) >= 57),
        ('45–56 (Watch)',     lambda s: 45 <= (get(s,'score') or 0) < 57),
        ('34–44 (Moderate)', lambda s: 34 <= (get(s,'score') or 0) < 45),
        ('<34  (Weak)',       lambda s: (get(s,'score') or 0) < 34),
    ]
    result = []
    for label, fn in BANDS:
        matched = [s for s in signals if fn(s) and s.get('outcome') in ('WIN','LOSS')]
        n    = len(matched)
        wins = sum(1 for s in matched if s['outcome'] == 'WIN')
        rets = [s.get('outcome_ret') for s in matched if s.get('outcome_ret') is not None]
        wr   = wins / n if n > 0 else None
        result.append({
            'label':    label,
            'n':        n,
            'win':      wins,
            'win_rate': round(wr, 3) if wr is not None else None,
            'lift':     round(wr / baseline_wr, 2) if (wr is not None and baseline_wr > 0) else None,
            'avg_ret':  round(sum(rets)/len(rets), 2) if rets else None,
        })
    return result


def compute_prev_stage_effect(signals, baseline_wr):
    """Was the stock in a better stage before entry? Effect on outcome."""
    stages = ['none','trending','coiling','breakout','pre_cross','post_cross','pullback']
    lookbacks = ['d_minus_5', 'd_minus_10', 'd_minus_20']
    result = []
    for lb in lookbacks:
        groups = defaultdict(lambda: {'win':0,'loss':0})
        for s in signals:
            if s.get('outcome') not in ('WIN','LOSS'):
                continue
            ps = (s.get('prev_stages') or {}).get(lb)
            if ps is None:
                continue
            groups[ps]['win' if s['outcome']=='WIN' else 'loss'] += 1
        buckets = []
        for stage, c in groups.items():
            n = c['win'] + c['loss']
            wr = c['win'] / n if n > 0 else 0
            buckets.append({'label': stage, 'n': n, 'win': c['win'], 'loss': c['loss'],
                            'win_rate': round(wr,3),
                            'lift': round(wr/baseline_wr,2) if baseline_wr>0 else 1.0})
        buckets.sort(key=lambda b: -b['win_rate'])
        result.append({'lookback': lb, 'buckets': buckets})
    return result


# ─── main ────────────────────────────────────────────────────────────────────

def run(dry_run=False):
    signals = load_signals()
    resolved = [s for s in signals if s.get('outcome') in ('WIN', 'LOSS')]

    total_win  = sum(1 for s in resolved if s['outcome'] == 'WIN')
    total_loss = len(resolved) - total_win
    baseline_wr = total_win / len(resolved) if resolved else None
    n_resolved  = len(resolved)

    print(f'[MODEL] Resolved signals: {n_resolved}  (WIN={total_win} LOSS={total_loss})')

    if n_resolved < MIN_RESOLVED:
        print(f'[MODEL] Insufficient data ({n_resolved}/{MIN_RESOLVED}). Writing placeholder.')
        model = {
            'computed_at':    datetime.datetime.now().isoformat(timespec='seconds'),
            'n_resolved':     n_resolved,
            'min_needed':     MIN_RESOLVED,
            'baseline_win_rate': baseline_wr,
            'features':       [],
            'combinations':   [],
            'rsi_adx_heatmap':[],
            'score_bands':    [],
            'prev_stage_effect': [],
        }
    else:
        print(f'[MODEL] Baseline win rate: {baseline_wr:.1%}')

        # Feature attribution
        features = []
        for name, type_, getter in FEATURE_DEFS:
            feat = compute_feature(resolved, name, type_, getter, baseline_wr, total_win, total_loss)
            # Skip features with only 1 bucket (no discrimination)
            if len(feat['buckets']) >= 2:
                features.append(feat)
        features.sort(key=lambda f: -f['iv'])

        print(f'[MODEL] Features computed: {len(features)}')
        for f in features[:5]:
            print(f'  IV={f["iv"]:.4f}  {f["name"]}')

        combos   = compute_boolean_combos(resolved, baseline_wr)
        heatmap  = compute_rsi_adx_heatmap(resolved, baseline_wr)
        bands    = compute_score_bands(resolved, baseline_wr)
        prev_eff = compute_prev_stage_effect(resolved, baseline_wr)

        model = {
            'computed_at':       datetime.datetime.now().isoformat(timespec='seconds'),
            'n_resolved':        n_resolved,
            'min_needed':        MIN_RESOLVED,
            'baseline_win_rate': round(baseline_wr, 3),
            'features':          features,
            'combinations':      combos,
            'rsi_adx_heatmap':   heatmap,
            'score_bands':       bands,
            'prev_stage_effect': prev_eff,
        }

    if dry_run:
        print('[MODEL] Dry run — not saving.')
        return model

    os.makedirs(os.path.dirname(REGRESSION_FILE), exist_ok=True)
    with open(REGRESSION_FILE, 'w', encoding='utf-8') as f:
        json.dump(model, f, ensure_ascii=False, indent=2)
    print(f'[MODEL] Saved -> {REGRESSION_FILE}')
    return model


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    run(dry_run=args.dry_run)
