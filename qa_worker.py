"""
qa_worker.py — Post-EVALS QA
==============================
Runs after eval_worker completes. Two groups of checks:

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
GROUP 1 — ASSERTIONS
These are the seven things that MUST be true every single day without
exception. If any of these fail, the Stock-Wise screen is showing you
wrong or incomplete information right now.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

A0a Each phase (Breakout, Pre-Cross, Post-Cross, Coiling, Pullback,
    Trending) contributes exactly 3 stocks to Stock-Wise — no more,
    no less (unless the phase itself has fewer than 3 stocks today).

    The scanner ranks stocks within each phase and picks the top 3.
    Stock-Wise is supposed to show exactly 3 per phase — that is the
    contract. If A0a fails for a phase it means the selection produced
    fewer than 3 stocks even though the phase has enough candidates.
    The most likely cause is a scoring or filtering bug that silently
    dropped stocks from the ranking before the top-3 was cut. The user
    sees fewer rows in that phase than they should.

A0b The overall top-3 (All tab) only adds stocks that are NOT already
    in Stock-Wise via a phase bucket.

    The All tab picks the top-3 highest-scoring stocks across all phases
    combined. But if any of those 3 are already in Stock-Wise because they
    ranked top-3 in their specific phase, they should NOT be added again
    — that would be a duplicate row. A0b checks that deduplication worked
    correctly: every stock in the All-tab additions is genuinely new and
    was not already picked by a phase. If this fails, the same stock
    appears twice in Stock-Wise under two different labels.

A1  Every stock that ranked top-3 today is actually tracked in Stock-Wise.

    Think of the scanner as a shortlist that updates every evening. When a
    stock makes the shortlist for the first time, the system is supposed to
    start tracking it — record its entry price, start the 60-day clock, show
    it in the Stock-Wise table. If A1 fails it means a stock made the
    shortlist today but was silently not added. It will never appear in
    Stock-Wise unless someone manually fixes it. The user sees a shorter
    table than they should, with no indication anything is missing.

A2  Every stock that ranked top-3 today is VISIBLE in the Stock-Wise tab
    (not hidden in the background tracking bucket).

    The system tracks two categories of stocks: "signal" (shown in Stock-
    Wise) and "baseline" (tracked silently for internal analysis, never
    shown). If a stock ranks top-3 but stays in the baseline bucket, the
    user never sees it. A2 catches the case where the stock IS being tracked
    but was never promoted from the hidden bucket to the visible one.
    The difference from A1: the entry exists, it's just invisible.

A3  Every stock that ranked top-3 today has its golden highlight marker
    for today's date on its row in Stock-Wise.

    The golden vertical line on a row means "this stock was in the top-3
    again today." It is how the user knows whether a stock that entered
    Stock-Wise weeks ago is STILL ranking high — a persistent golden line
    means strong, consistent signal. If A3 fails, the stock IS visible and
    IS being tracked, but there is no golden marker for today. The user
    would see a row that looks like it dropped off the shortlist even though
    it didn't. Conviction strength looks weaker than it really is.

A4  No stock appears twice in Stock-Wise as two separate active entries.

    Each stock should have exactly one active tracking entry at any time.
    If a stock gets entered twice (an old entry that was never closed, plus
    a new one), both rows show up in Stock-Wise. The analytics (win rate,
    average return) count the same stock twice. The older row may show
    the wrong entry price and wrong return %. This is the "ghost signal"
    problem — the row looks real but the numbers are stale or wrong.

A5  Every Stock-Wise stock that is still active (not yet resolved as WIN,
    LOSS, or EXPIRED) has today's closing price recorded.

    This check only applies to stocks whose journey is still ongoing.
    Once a stock has finished — hit +15%, hit -9%, or run out of its
    60 days — we stop recording new prices for it. That is correct
    behaviour. But for stocks that are still running, today's price
    must be there.

    The return percentages in the matrix (D1, D2, D3 ... columns) are
    computed by comparing each day's closing price to the entry price.
    If today's price is missing for a still-active stock, today's column
    is blank — the user cannot tell if the stock went up or down. Worse,
    if the stock was close to the +15% or -9% threshold, the system
    cannot automatically detect and record the outcome until the next
    day when a price arrives. The most common cause is the stock being
    moved to a restricted trading segment by the exchange (T2T or Book
    Entry) so Kite returns no data for it.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
GROUP 2 — BUG DETECTORS
These are patterns we have seen cause silent data corruption in the past.
They do not necessarily break today's display, but they mean historical
data is wrong — win rates, return percentages, and entry dates in the
archive are incorrect. Each one has happened in production at least once.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

B1  No stock has its entry date recorded as a Saturday or Sunday.

    Markets are closed on weekends. A stock's entry date (and all price
    dates) should only ever be Monday through Friday. If B1 fires, it
    means the evening pipeline ran very late — past midnight on a Friday —
    and accidentally stamped Saturday's date instead of Friday's. The stock
    then appears to have entered Stock-Wise on a day the market was closed.
    Its entry price (D0) may be wrong, and return percentages counted from
    that date will all be off. Seen on 28-Apr-2026 and 28-Mar-2026.

B2  No stock that finished (WIN / LOSS / EXPIRED) is missing its finish day
    number.

    When a stock hits +15% or -9% or runs out of time, the system records
    which day of the journey that happened on (Day 4, Day 12, etc.). This
    number is used to grey out the remaining empty columns in the matrix
    row — without it, the matrix keeps showing blank cells after the stock
    already finished, which looks like missing data rather than a completed
    trade. B2 catches cases where the outcome was partially written and
    the day number got left blank.

B3  No WIN result has a return percentage below +14%.

    A WIN means the stock gained +15% from its entry price. The system
    flags a 1% tolerance for rounding. If a stock is marked WIN but shows
    e.g. +0.4% or +6%, something went wrong with how the entry price was
    chosen as the baseline. We have seen this happen when the system used
    the price from weeks before the stock actually entered Stock-Wise,
    making a tiny gain look like a 15% WIN. On 4-May-2026 there were 14
    such false WINs — the win rate jumped from 70% to 77% overnight due to
    this bug. B3 catches if it ever happens again.

B4  No LOSS result has a return percentage above -8%.

    Same wrong-baseline problem as B3, but in the other direction. A LOSS
    means the stock fell -9% from entry. If a LOSS shows e.g. -0.3% or
    -2%, the entry price baseline was wrong — the system measured from a
    price where the stock had already fallen a lot before entering Stock-
    Wise, so a tiny additional drop triggered the loss threshold incorrectly.

B5  No stock has its entry date also appearing as a "golden line" day in
    its history of top-3 appearances.

    The day a stock first enters Stock-Wise is its entry day — Day 0. The
    golden line markers start from Day 1 onward, each time the stock
    re-appears in the top-3. A previous bug caused the system to also
    record Day 0 as a golden line appearance, counting the entry itself
    as if it were a retrigger. This made every stock look like it had an
    immediate golden line on its very first day, which is misleading — Day 0
    is always there, the golden line is only meaningful from Day 1 onward.
    On 7-May-2026 this affected 118 stocks simultaneously.

B6  No stock has an entry date that does not match an actual top-3 ranking
    on that date.

    The entry date in Stock-Wise is supposed to be the first day the stock
    ranked in the top-3 of its phase. If B6 fires, it means the entry date
    was set to a day when the stock was NOT actually in the top-3 — usually
    caused by a manual data repair script that set dates using old, incorrect
    scoring (the scoring bug we fixed on 7-May-2026 where stored T-scores
    were stale). This matters because the entry date determines the entry
    price, which is the baseline for all return % calculations. A wrong
    entry date = wrong entry price = all returns for that stock are wrong.

    IMPORTANT — fixing B6 always requires two steps, in this order:
      1. Correct tier_date to the first date the stock actually ranked top-3.
      2. Strip the new tier_date from retrigger_dates if it appears there.
    Step 2 is necessary because the old tier_date or new tier_date may already
    exist as a retrigger entry, which would then trigger B5. Always run QA
    again after a B6 fix to catch any B5 issues it introduced. Seen in
    practice on 8-May-2026: fixing 13 B6 stocks immediately created 7 B5
    violations for the same stocks because their new tier_dates matched
    existing retrigger entries.

Writes:
  data/status/qa.json    — latest run status (same shape as other workers)
  data/qa_log.json       — one record per trading date (appended/updated)
"""

import sys, os, json, datetime, sqlite3
from collections import Counter

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
DB_FILE     = os.path.join(BASE_DIR, 'data', 'dalal_street.db')
STATUS_FILE = os.path.join(BASE_DIR, 'data', 'status', 'qa.json')
LOG_FILE    = os.path.join(BASE_DIR, 'data', 'qa_log.json')

PHASES = ['post_cross', 'pre_cross', 'breakout', 'coiling', 'pullback', 'trending']


def _write_status(status, count=None, errors=0, duration=None, message=''):
    obj = {
        'status':   status,
        'saved_at': datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat(),
        'count':    count,
        'errors':   errors,
        'duration': duration,
        'message':  message,
    }
    os.makedirs(os.path.dirname(STATUS_FILE), exist_ok=True)
    with open(STATUS_FILE, 'w', encoding='utf-8') as f:
        json.dump(obj, f)


# ── Scoring — identical to eval_worker (recomputed from raw RSI/ADX, never stored tScore) ──

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

def _is_weekend(date_str):
    try:
        return datetime.date.fromisoformat(date_str).weekday() >= 5
    except Exception:
        return False


_DATE_OVERRIDE = None


def run():
    import time as _t
    t0 = _t.time()
    _write_status('running')

    # Each issue: {group, check, ticker, detail}
    # group = 'ASSERT' or 'BUG'
    issues = []

    def fail(group, check, ticker, detail):
        issues.append({'group': group, 'check': check,
                       'ticker': ticker or '—', 'detail': detail})

    try:
        con = sqlite3.connect(f'file:{DB_FILE}?mode=ro', uri=True)
        con.row_factory = sqlite3.Row

        # ── Trading date: override for historical re-runs, else from stocks_live ──
        if _DATE_OVERRIDE:
            trading_date = _DATE_OVERRIDE
            has_date = con.execute(
                'SELECT 1 FROM stocks_master WHERE trading_date=? LIMIT 1', (trading_date,)
            ).fetchone()
            if not has_date:
                _write_status('error', errors=1,
                              message=f'No stocks_master data for {trading_date}')
                print(f'[QA] No stocks_master data for {trading_date}')
                return
        else:
            td_row = con.execute('SELECT DISTINCT trading_date FROM stocks_live LIMIT 1').fetchone()
            if not td_row:
                _write_status('error', errors=1, message='No trading_date in stocks_live')
                return
            trading_date = td_row[0]

        # ── Scanner stocks: live for today, stocks_master for past dates ──────
        if _DATE_OVERRIDE:
            stocks = [dict(r) for r in con.execute(
                "SELECT ticker, stage, fScore, rsi, adx, upsidePct, mcap, "
                "emaPreCross, vpbScore, crossScore "
                "FROM stocks_master WHERE trading_date=? AND stage != 'none'",
                (trading_date,)
            ).fetchall()]
        else:
            stocks = [dict(r) for r in con.execute(
                "SELECT ticker, stage, fScore, rsi, adx, upsidePct, mcap, "
                "emaPreCross, vpbScore, crossScore "
                "FROM stocks_live WHERE stage != 'none'"
            ).fetchall()]

        # ── Build expected selected set (same logic as eval_worker) ───────────
        selected = {}        # ticker -> phase label
        phase_tickers = set()
        for phase in PHASES:
            bucket = sorted([s for s in stocks if s['stage'] == phase], key=phase_key)[:3]
            for s in bucket:
                phase_tickers.add(s['ticker'])
                selected[s['ticker']] = phase
        for s in sorted(stocks, key=all_key)[:3]:
            if s['ticker'] not in phase_tickers:
                selected[s['ticker']] = 'all_tab'

        # ── All OPEN signals ──────────────────────────────────────────────────
        all_open = [dict(r) for r in con.execute(
            "SELECT ticker, id, tier, tier_date, outcome, retrigger_dates, day0 "
            "FROM evals_signals WHERE outcome='OPEN'"
        ).fetchall()]

        # Most-recent OPEN signal per ticker
        open_by_ticker = {}
        for s in all_open:
            t = s['ticker']
            prev = open_by_ticker.get(t)
            if not prev or s['day0'] > prev['day0']:
                open_by_ticker[t] = s

        # Prices written today (set of signal_ids)
        price_today = {r['signal_id'] for r in con.execute(
            "SELECT DISTINCT signal_id FROM evals_daily_prices WHERE trading_date=?",
            (trading_date,)
        ).fetchall()}

        # All OPEN signal-tier signals
        signal_tier_open = [s for s in all_open if s['tier'] == 'signal']

        # All resolved signal-tier signals
        resolved_signal = [dict(r) for r in con.execute(
            "SELECT ticker, id, outcome, outcome_day, outcome_ret "
            "FROM evals_signals WHERE outcome IN ('WIN','LOSS','EXPIRED') AND tier='signal'"
        ).fetchall()]

        con.close()

        # ════════════════════════════════════════════════════════════════════
        # GROUP 1 — ASSERTIONS
        # ════════════════════════════════════════════════════════════════════

        # A0a — Every phase that has stocks today shows exactly 3 in Stock-Wise
        #        (or fewer only if the phase itself has fewer than 3 stocks total)
        for phase in PHASES:
            all_in_phase   = [s for s in stocks if s['stage'] == phase]
            in_selected    = [t for t, p in selected.items() if p == phase]
            expected_count = min(3, len(all_in_phase))
            if len(in_selected) < expected_count:
                fail('ASSERT', 'A0a', None,
                     f'Phase {phase}: expected {expected_count} stocks in selection '
                     f'but only {len(in_selected)} present '
                     f'({len(all_in_phase)} stocks in phase today)')

        # A0b — All-tab additions must be stocks NOT already selected via a phase bucket.
        #        The all-tab pool picks the top-3 overall scorers, but only adds them
        #        if they are not already in Stock-Wise via a phase. If any all-tab stock
        #        is a duplicate of a phase stock, it means deduplication failed and the
        #        same stock is being counted twice in the selection.
        all_tab_tickers  = [t for t, p in selected.items() if p == 'all_tab']
        for ticker in all_tab_tickers:
            if ticker in phase_tickers:
                fail('ASSERT', 'A0b', ticker,
                     f'Stock appears in both a phase bucket AND the all-tab additions — '
                     f'deduplication failed, this stock is being counted twice in selection')

        # A1 — Every selected stock has an OPEN signal
        for ticker, phase in sorted(selected.items()):
            if open_by_ticker.get(ticker) is None:
                fail('ASSERT', 'A1', ticker,
                     f'[{phase}] in top-3 today but no OPEN signal in evals_signals')

        # A2 — Every selected stock has tier='signal'
        for ticker, phase in sorted(selected.items()):
            sig = open_by_ticker.get(ticker)
            if sig and sig['tier'] != 'signal':
                fail('ASSERT', 'A2', ticker,
                     f'[{phase}] in top-3 today but tier={sig["tier"]} — must be signal')

        # A3 — Every selected stock has tier_date=today OR retrigger for today
        for ticker, phase in sorted(selected.items()):
            sig = open_by_ticker.get(ticker)
            if not sig or sig['tier'] != 'signal':
                continue  # A1/A2 already caught this
            rdates = {(r['date'] if isinstance(r, dict) else r)
                      for r in json.loads(sig['retrigger_dates'] or '[]')}
            if sig['tier_date'] != trading_date and trading_date not in rdates:
                fail('ASSERT', 'A3', ticker,
                     f'[{phase}] tier_date={sig["tier_date"]}, '
                     f'no retrigger for {trading_date} — golden line missing today')

        # A4 — No ticker has more than one OPEN signal
        for ticker, cnt in Counter(s['ticker'] for s in all_open).items():
            if cnt > 1:
                fail('ASSERT', 'A4', ticker,
                     f'{cnt} OPEN signals exist simultaneously — ghost duplicate cycle')

        # A5 — Every still-active (outcome=OPEN) signal-tier signal has a price for today.
        #      Resolved signals (WIN/LOSS/EXPIRED) are correctly excluded — we stop
        #      writing prices once a trade finishes. signal_tier_open is already
        #      filtered to outcome='OPEN' so resolved signals never reach this check.
        #      For historical re-runs: skip signals whose day0 > trading_date (didn't exist yet).
        for sig in signal_tier_open:
            if sig.get('day0', '') > trading_date:
                continue  # signal created after this date — not expected to have a price
            if sig['id'] not in price_today:
                fail('ASSERT', 'A5', sig['ticker'],
                     f'Still-active signal has no price for {trading_date} — '
                     f'stock not in today\'s Kite scan '
                     f'(may be suspended or moved to -ST/-BE segment) id={sig["id"]}')

        # ════════════════════════════════════════════════════════════════════
        # GROUP 2 — BUG DETECTORS
        # ════════════════════════════════════════════════════════════════════

        # B1 — No tier_date or day0 stamped on a weekend
        #      Bug: eval_worker used system clock instead of trading_date when pipeline
        #      ran past midnight (e.g. Saturday 00:10 after Friday market close)
        for sig in signal_tier_open:
            if sig['tier_date'] and _is_weekend(sig['tier_date']):
                fail('BUG', 'B1', sig['ticker'],
                     f'tier_date={sig["tier_date"]} is a weekend — '
                     f'pipeline ran past midnight and used system clock instead of trading_date')
            if sig['day0'] and _is_weekend(sig['day0']):
                fail('BUG', 'B1', sig['ticker'],
                     f'day0={sig["day0"]} is a weekend — '
                     f'pipeline ran past midnight and used system clock instead of trading_date')

        # B2 — Resolved signals must have outcome_day set
        #      Bug: compute_outcome() wrote outcome= but crashed/returned early before
        #      setting outcome_day, leaving a half-resolved record
        for sig in resolved_signal:
            if sig['outcome_day'] is None:
                fail('BUG', 'B2', sig['ticker'],
                     f'outcome={sig["outcome"]} but outcome_day=NULL — '
                     f'half-written record, frontend will not stop matrix at right column '
                     f'(id={sig["id"]})')

        # B3 — WIN signals must have outcome_ret >= 14%
        #      Bug: outcome computed from wrong baseline (tier_date=NULL → fell back to
        #      price_d0 which was weeks before Stock-Wise entry). Seen 2026-05-04:
        #      14 false WINs at near-zero return because recompute ran before tier update
        for sig in resolved_signal:
            if sig['outcome'] == 'WIN' and sig['outcome_ret'] is not None:
                if sig['outcome_ret'] < 14.0:
                    fail('BUG', 'B3', sig['ticker'],
                         f'WIN with outcome_ret={sig["outcome_ret"]}% — '
                         f'too low, WIN threshold is +15%; likely computed from wrong '
                         f'baseline price (tier_date was NULL at outcome time) id={sig["id"]}')

        # B4 — LOSS signals must have outcome_ret <= -8%
        #      Same wrong-baseline bug as B3, loss direction
        for sig in resolved_signal:
            if sig['outcome'] == 'LOSS' and sig['outcome_ret'] is not None:
                if sig['outcome_ret'] > -8.0:
                    fail('BUG', 'B4', sig['ticker'],
                         f'LOSS with outcome_ret={sig["outcome_ret"]}% — '
                         f'too high, LOSS threshold is -9%; likely computed from wrong '
                         f'baseline price (tier_date was NULL at outcome time) id={sig["id"]}')

        # B5 — No retrigger_dates entry equals the signal's own tier_date
        #      Bug: old check_retriggers() recorded a retrigger on the promotion day itself
        #      (tier_date == today). The entry day is not a retrigger. Seen 2026-05-07:
        #      118 signals had tier_date duplicated in retrigger_dates, inflating golden line
        for sig in signal_tier_open:
            if not sig['tier_date']:
                continue
            rdates = {(r['date'] if isinstance(r, dict) else r)
                      for r in json.loads(sig['retrigger_dates'] or '[]')}
            if sig['tier_date'] in rdates:
                fail('BUG', 'B5', sig['ticker'],
                     f'tier_date={sig["tier_date"]} also in retrigger_dates — '
                     f'entry day counted as retrigger, inflates golden line '
                     f'and shifts D0 display (id={sig["id"]})')

        # B6 — tier_date must be a date when the stock actually ranked top-3
        #      Bug: backfill runs or wrong promotion set tier_date to a date where the stock
        #      was not in the scanner top-3, making the D0 baseline price wrong
        #      Only checkable if stocks_master has data for that date
        #      FIX PROCEDURE: (1) correct tier_date to first real top-3 date,
        #      (2) strip the new tier_date from retrigger_dates (or B5 will fire next).
        #      Always re-run QA after fixing B6 — it reliably creates B5 violations.
        #      Suppressed for stocks that never ranked top-3 anywhere in stocks_master
        #      (unfixable legacy backfill errors — correct date simply doesn't exist).
        con3 = sqlite3.connect(f'file:{DB_FILE}?mode=ro', uri=True)
        con3.row_factory = sqlite3.Row

        # Build full top-3 cache once: date -> set of tickers in top-3
        # This avoids O(signals × dates) repeated full-table scans
        _top3_cache = {}  # trading_date -> frozenset of tickers
        _all_master_rows = con3.execute(
            "SELECT trading_date, ticker, stage, fScore, rsi, adx, upsidePct, mcap, "
            "emaPreCross, vpbScore, crossScore FROM stocks_master WHERE stage != 'none'"
        ).fetchall()
        _by_date = {}
        for row in _all_master_rows:
            d = row[0]
            if d not in _by_date:
                _by_date[d] = []
            _by_date[d].append(dict(zip(
                ['trading_date','ticker','stage','fScore','rsi','adx','upsidePct',
                 'mcap','emaPreCross','vpbScore','crossScore'], row
            )))
        for d, hs in _by_date.items():
            sel = set(); ph = set()
            for phase in PHASES:
                for s in sorted([s for s in hs if s['stage'] == phase], key=phase_key)[:3]:
                    ph.add(s['ticker']); sel.add(s['ticker'])
            for s in sorted(hs, key=all_key)[:3]:
                if s['ticker'] not in ph:
                    sel.add(s['ticker'])
            _top3_cache[d] = frozenset(sel)

        # Tickers that appear in top-3 on at least one date (used to suppress unfixable B6)
        _ever_top3 = set()
        for sel in _top3_cache.values():
            _ever_top3.update(sel)

        for sig in signal_tier_open:
            td = sig['tier_date']
            if not td or td not in _top3_cache:
                continue
            if sig['ticker'] not in _top3_cache[td]:
                # Only flag if a correct date actually exists (stock appeared in top-3 somewhere)
                if sig['ticker'] in _ever_top3:
                    fail('BUG', 'B6', sig['ticker'],
                         f'tier_date={td} but stock was NOT in top-3 on that date — '
                         f'tier_date set incorrectly (wrong backfill or bad promotion) '
                         f'id={sig["id"]}')
        con3.close()

        # ════════════════════════════════════════════════════════════════════
        # BUILD RESULT ROWS for UI / qa_log
        # ════════════════════════════════════════════════════════════════════
        rows = []

        # Primary rows: one per selected stock (always shown, status=OK or ISSUE)
        for ticker, phase in sorted(selected.items(), key=lambda x: (x[1], x[0])):
            sig = open_by_ticker.get(ticker)
            ticker_assert_issues = [i for i in issues
                                    if i['ticker'] == ticker and i['group'] == 'ASSERT']
            if not ticker_assert_issues:
                tier_date = sig['tier_date']
                rdates    = {(r['date'] if isinstance(r, dict) else r)
                             for r in json.loads(sig['retrigger_dates'] or '[]')}
                entry_type = 'NEW ENTRY' if tier_date == trading_date else 'RETRIGGER'
                rows.append({'ticker': ticker, 'phase': phase, 'status': 'OK',
                             'entry_type': entry_type,
                             'detail': f'tier_date={tier_date}'})
            else:
                for i in ticker_assert_issues:
                    rows.append({'ticker': ticker, 'phase': phase, 'status': 'ISSUE',
                                 'entry_type': i['check'], 'detail': i['detail']})

        # Secondary rows: bug detector findings (not tied to today's selection)
        for i in issues:
            if i['group'] == 'BUG':
                rows.append({'ticker': i['ticker'], 'phase': '—', 'status': 'BUG',
                             'entry_type': i['check'], 'detail': i['detail']})

        elapsed     = round(_t.time() - t0, 1)
        assert_fail = sum(1 for i in issues if i['group'] == 'ASSERT')
        bug_fail    = sum(1 for i in issues if i['group'] == 'BUG')
        issue_count = len(issues)

        if issue_count == 0:
            summary = 'All OK'
        else:
            parts = []
            if assert_fail: parts.append(f'{assert_fail} assertion(s) failed')
            if bug_fail:    parts.append(f'{bug_fail} bug(s) detected')
            summary = ' · '.join(parts)

        # ── Write qa_log.json ─────────────────────────────────────────────────
        record = {
            'trading_date':  trading_date,
            'run_at':        datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat(),
            'total':         len(rows),
            'issues':        issue_count,
            'assert_failed': assert_fail,
            'bugs_detected': bug_fail,
            'summary':       summary,
            'rows':          rows,
        }
        log = []
        if os.path.exists(LOG_FILE):
            try:
                with open(LOG_FILE, encoding='utf-8') as f:
                    log = json.load(f)
            except Exception:
                log = []
        log = [e for e in log if e.get('trading_date') != trading_date]
        log.append(record)
        log.sort(key=lambda e: e.get('trading_date', ''), reverse=True)
        with open(LOG_FILE, 'w', encoding='utf-8') as f:
            json.dump(log, f, indent=2)

        _write_status('done', count=len(rows), errors=issue_count,
                      duration=elapsed, message=summary)
        print(f'[QA] {summary} | {len(selected)} selected · {len(signal_tier_open)} signal-tier open | {elapsed}s')
        if issues:
            for i in issues:
                print(f'  [{i["group"]}:{i["check"]}] {i["ticker"]}: {i["detail"]}')

    except Exception as e:
        elapsed = round(_t.time() - t0, 1)
        _write_status('error', errors=1, duration=elapsed, message=str(e))
        print(f'[QA] ERROR: {e}')
        raise


if __name__ == '__main__':
    import argparse, re as _re
    parser = argparse.ArgumentParser(description='QA worker')
    parser.add_argument('--date', default=None,
                        help='Override trading date for historical re-run (YYYY-MM-DD)')
    args = parser.parse_args()
    if args.date:
        if not _re.match(r'^\d{4}-\d{2}-\d{2}$', args.date):
            print(f'[QA] Invalid --date format: {args.date}')
            sys.exit(1)
        _DATE_OVERRIDE = args.date
        print(f'[QA] Date override: {_DATE_OVERRIDE}')
    run()
