# INSTRUCTIONS FOR STOCK-WISE TAB (EVALS)

## Scope
Signal-tier stocks only — top 3 per phase (POST-CROSS, PRE-CROSS, BREAKOUT, COILING, PULLBACK, TRENDING)
plus top 3 all-tab by F+T+S score, deduplicated.
Baseline-tier stocks (remaining 2500+) are tracked in signals_log.json but NOT shown here.

## When Stocks Are Added
Stocks are added to this view **only after EOD** — when `eval_worker.py --eod` runs at ~3:35 PM Mon–Fri.
Never add stocks intraday. D0 price = closing price from Kite OHLCV data, not an intraday LTP.

## Selection Logic
- **Phase-tab top-3**: sort by F+T only (fScore + rsiScore + adxScore), tiebreak upsidePct desc → mcap desc
- **All-tab top-3**: sort by F+T+S (F+T + signal score), same tiebreaks — added only if not already selected
- A stock already in an open signal is NOT re-logged as a new D0; it continues its existing cycle

## Day Columns (D0, D1, D2 …)
- **D0**: entry date (first EOD after selection). Price = closing price. Return = "—" (base day).
- **D1+**: each subsequent trading day the EOD run fires. Price = closing price. Return = % vs D0 price.
- Day index = **trading days elapsed** since D0 (i.e., index in the `prices{}` dict, not calendar days). Weekends and holidays are skipped — D1 is the next trading day after D0.

## Golden Vertical Line Rule
A **golden left-border** (`border-left: 3px solid #d4a017`) appears on a day cell when:
> The stock was in today's top-3 selection — either top-3 of any phase (by F+T) or all-tab top-3 (by F+T+S).

Implemented by `check_retriggers(signals, eligible_tickers)` in `eval_worker.py`, which receives the `eligible_tickers` set from `update_signal_tiers()` (today's top-3 per phase + all-tab top-3). Only stocks in that set get a retrigger date recorded.

- A stock can be re-selected in a different phase than its D0 phase — golden line still fires.
- If a stock is signal-tier but not in today's top-3, no golden line appears that day.
- `check_retriggers()` runs after `update_signal_tiers()` in the EOD sequence.

## Phase Badge Rule
A **colored phase pill** appears on a day cell when the stock's stage changed from the previous day's stage.
- Badge is shown ONLY on the day of change — subsequent days with the same stage show nothing (no badge, no text).
- This keeps cells compact. The absence of a badge means "same phase as yesterday."
- Phase color mapping: POST-X green · PRE-X teal · BRK gold · COIL grey · PULL blue · TREND purple

## Win / Stop Zone
- **WIN zone** (price ≥ D0 price × 1.15): cell background tinted green, return % shown in bright green bold
- **STOP zone** (price ≤ D0 price × 0.91): cell background tinted red, return % shown in bright red bold
- These are visual indicators only — actual WIN/LOSS outcome is computed by eval_worker

## Outcome Badges (Stock Column)
Shown inline next to the ticker after the signal closes:
- `+18.4%▲` green pill → WIN
- `-9.1%▼` red pill → LOSS
- `+3.2%` grey pill → EXPIRED (ran 60 calendar days without hitting WIN or LOSS)

## Price in Day Cells
Prices are abbreviated for space:
- `₹234` (< ₹1,000)
- `₹1.23k` (₹1,000–₹9,999)
- `₹12.3k` (≥ ₹10,000)

## Data Source
- Prices: `data/signals_log.json` → `prices` dict keyed by date string
- Stage history: `data/signals_log.json` → `stage_history` array
- Re-trigger dates: `data/signals_log.json` → `retrigger_dates` array

## Analytics Tab — Same Data, Different View
- The **Analytics tab** (win-rate charts, score tiers, equity curve) reads the same `signals_log.json` as Stock-Wise
- Analytics scope = signal-tier stocks only (same ~15–20 stock pool)
- Analytics is a different rendering of the same dataset — it does NOT fetch `/api/lifecycle`

## Separation from Lifecycle Tab
- Stock-Wise + Analytics scope = signal-tier only (15–20 stocks max)
- Lifecycle scope = ALL 2535 NSE stocks
- These two sections read different data sources (`signals_log.json` vs `lifecycle_log.json`) and must NEVER share filter state
