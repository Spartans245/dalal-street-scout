# Instructions for EVALS Development

> Last updated: 2026-03-25
> This document covers the core architecture, data design, worker logic, and analysis goals shared across all three EVALS sub-systems. For tab-specific display and operational rules, see:
> - `INSTRUCTIONS_FOR_STOCKWISE.md` — Stock-Wise and Analytics tabs
> - `INSTRUCTIONS_FOR_LIFECYCLE.md` — Lifecycle tab and lifecycle_worker

---

## Part 1 — Core Architecture Rules

### Rule 1: Never modify existing scanner workers for EVALS
The following files must NOT be touched for EVALS purposes:
- `kite_worker.py`
- `compute.py`
- `nse_worker.py`
- `yf_worker.py`
- `orchestrator.py`

**Reason:** EVALS must never increase scanner runtime or risk breaking the existing data pipeline. The scanner runs on a fixed Task Scheduler schedule and must remain isolated.

### Rule 2: All signal-logging logic lives in `eval_worker.py`
`eval_worker.py` is the dedicated EVALS worker for signal-tier stocks. It runs independently of the scan pipeline and handles all signal logging, price tracking, stage history, and outcome computation.

### Rule 3: EVALS has its own data files — never mix with scanner output
| File | Purpose | Access |
|------|---------|--------|
| `data/computed/stocks.json` | Scanner output — stocks with technicals | READ only |
| `data/raw/kite.json` | Raw Kite OHLCV | READ only |
| `data/raw/nse.json` | NSE universe + fundamentals | READ only |
| `data/status/*.json` | Worker status | READ only |
| `data/signals_log.json` | EVALS signal log (Stock-Wise + Analytics) | READ + WRITE (eval_worker-owned) |
| `data/evals/analytics.json` | Pre-aggregated analytics | READ + WRITE (analytics_worker-owned) |
| `data/evals/nifty_close.json` | NIFTY EOD close (written by kite_worker) | READ only |
| `data/lifecycle_log.json` | Lifecycle tracking — ALL 2535 stocks | READ + WRITE (lifecycle_worker-owned) |

### Rule 4: One allowed exception for scanner workers
If a new data point is needed **at scan time** (computed as a side-effect of the scan, no extra API calls, < 5ms per stock), it may be added to `compute.py`. This is a last resort. All other EVALS data must be derived from existing scanner output.

### Rule 5: `eval_worker.py` is the SOLE signal logger for signal-tier stocks
`compute.py`'s `log_signals()` function is disabled/deprecated. `eval_worker.py` is the only process that writes to `data/signals_log.json`.

### Rule 6: Three workers, three repos, strict isolation
| Worker | Data file | Scope |
|--------|-----------|-------|
| `eval_worker.py` | `data/signals_log.json` | Signal-tier stocks (Stock-Wise + Analytics tabs) |
| `analytics_worker.py` | `data/evals/analytics.json` | Aggregated analytics from signals_log |
| `lifecycle_worker.py` | `data/lifecycle_log.json` | ALL 2535 NSE stocks (Lifecycle tab only) |

**Absolute rule:** `lifecycle_worker` NEVER reads `signals_log.json`. `eval_worker` NEVER reads `lifecycle_log.json`.

---

## Part 2 — Signal Tracking Design (eval_worker scope)

### Scope: ALL 2535 NSE stocks baselined, signal-tier displayed
Every stock is logged in `signals_log.json` (tier = `signal` or `baseline`). Stock-Wise and Analytics tabs show only `tier='signal'` stocks. This gives a full population sample for analytics while keeping the UI focused.

**Signal-tier selection (per EOD run):**
- Phase-tab top-3: sort by F+T only (fScore + rsiScore + adxScore), tiebreak upsidePct desc → mcap desc
- All-tab top-3: sort by F+T+S (F+T + signal score), same tiebreaks — added only if not already selected

### D0 — The Entry Date
D0 = the calendar date when a signal entry is created for a stock.
- The global D0 for Cycle 1 is **2026-03-24** (all stocks baselined this day).
- Each subsequent cycle has its own D0 (see D0 Reset Rules below).

### Outcome Definitions
| Outcome | Condition |
|---------|-----------|
| **WIN** | Price first reaches ≥ entry_price × 1.15 (+15%) on any calendar day |
| **LOSS** | Price first reaches ≤ entry_price × 0.91 (−9%) on any calendar day |
| **EXPIRED** | 60 calendar days have elapsed from D0 with no WIN or LOSS |
| **OPEN** | None of the above — still tracking |

**WIN takes priority over LOSS** if both thresholds are crossed on the same day (e.g. a gap-up that also breaches stop — WIN is recorded).

### Tracking Window
- WIN/LOSS can trigger on any day (D1 onward). No minimum holding period.
- EXPIRED triggers when the date of a price entry ≥ D0 + 60 calendar days.
- Once a signal is WIN / LOSS / EXPIRED, it stops receiving price updates.

### D0 Reset Rules (Continuous Tracking)
When a stock resolves, a NEW signal entry is automatically created on the next EOD run:

| Prior Outcome | New D0 | New price_d0 |
|--------------|--------|--------------|
| **WIN** | `outcome_date` (the day +15% was hit) | `outcome_price` |
| **LOSS** | `outcome_date` (the day −9% was hit) | `outcome_price` |
| **EXPIRED** | `outcome_date + 1 calendar day` (the "61st day") | Current price from today's scan |

Each new cycle gets an incremented `cycle` number (1, 2, 3…) and unique ID: `{TICKER}_{new_d0}_c{cycle}`.

---

## Part 3 — Signal Schema (`data/signals_log.json`)

```json
{
  "signals": [
    {
      "id":              "RELIANCE_2026-03-24_c1",
      "ticker":          "RELIANCE",
      "name":            "Reliance Industries Ltd",
      "sector":          "Oil & Gas",
      "board":           "main",
      "day0":            "2026-03-24",
      "cycle":           1,
      "stage":           "breakout",
      "tier":            "signal",
      "source":          "eval_worker",

      "score":           48,
      "fScore":          24,
      "tScore":          16,
      "sigScore":        8,

      "rsi":             62.4,
      "adx":             28.1,

      "pe":              25.3,
      "mcap":            1850000,
      "debtEq":          0.4,
      "roe":             12.1,

      "ema14":           2810.5,
      "ema50":           2780.0,
      "emaGapPct":       0.011,
      "ema14Rising":     true,
      "emaCrossDays":    3,

      "vpbScore":        7,
      "vpbDetail":       "breakout",
      "crossScore":      0,
      "emaPreCross":     false,
      "emaCross":        true,
      "emaPostCross":    false,
      "emaPullback":     false,
      "volConfirm":      true,
      "priceCoiling":    true,
      "volShrinking":    true,

      "price_d0":        2850.0,
      "nifty_d0":        22450.0,
      "upsidePct_d0":    12.4,

      "prices":          {"2026-03-24": 2850.0, "2026-03-25": 2870.0},
      "nifty_prices":    {"2026-03-24": 22450.0, "2026-03-25": 22480.0},
      "stage_history": [
        {"date": "2026-03-24", "stage": "breakout", "day_n": 0},
        {"date": "2026-04-02", "stage": "pre_cross", "day_n": 7}
      ],
      "retrigger_dates": [
        {"date": "2026-03-25", "day_n": 1}
      ],

      "outcome":         "WIN",
      "outcome_day":     32,
      "outcome_date":    "2026-04-25",
      "outcome_price":   3277.5,
      "outcome_ret":     15.0
    }
  ]
}
```

### Field Notes
- `prices{}` — date → close price. Keys are trading days only (no weekends/holidays). Used for frontend matrix and outcome computation.
- `nifty_prices{}` — same date axis as `prices{}`. Used for NIFTY alpha calculation.
- `stage_history[]` — only records transitions (deduped). First entry is always D0. Used by `evStageAtDay()` in frontend to display stage on any given day.
- `retrigger_dates[]` — **active field** (not deprecated). Records dates when `check_retriggers()` confirmed the stock was still in an actionable stage. Shown as golden left-border in Stock-Wise matrix.
- `tier` — `"signal"` (shown in Stock-Wise/Analytics) or `"baseline"` (tracked but not displayed).

---

## Part 4 — eval_worker.py Reference

### Modes
```
python eval_worker.py --eod            # Full EOD update (log + prices + outcomes)
python eval_worker.py --price-refresh  # Intraday: update prices only (no snapshots)
```

### EOD Run Sequence (`run_eod()`)
1. Load `data/computed/stocks.json` (current scan results)
2. `backfill_tiers(signals)` — assign tier to any signals missing it (migration from older format)
3. `check_retriggers(signals, stocks)` — records today in `retrigger_dates[]` for any OPEN signal whose stock is still in any actionable stage (stage ≠ none)
4. `log_new_signals(signals, stocks)` — creates new cycle entries for stocks with no OPEN signal
5. `update_stage_history(signals, stocks)` — appends stage transitions for open signals
6. `update_prices(signals, stocks, nifty, mode='eod')` — appends price + NIFTY
7. `recompute_all_outcomes(signals)` — checks WIN/LOSS/EXPIRED for all OPEN signals
8. `save_signals(signals)` — atomic write to `data/signals_log.json`

### Key Functions
| Function | Description |
|----------|-------------|
| `compute_outcome(sig)` | WIN/LOSS/EXPIRED logic. WIN checked before LOSS. Expiry = D0 + 60 calendar days. |
| `check_retriggers(signals, stocks)` | Fires for any OPEN signal whose current stage is in `_ACTIONABLE_STAGES` (any non-none stage). Phase-agnostic — does not check top-3 ranking. |
| `log_new_signals(signals, stocks)` | Top-3 per phase (F+T sort) + all-tab top-3 (F+T+S). Creates new entries; D0 reset applied from prior resolved signal. |
| `update_stage_history(signals, stocks)` | Appends to `stage_history[]` only when stage changed from last entry. |
| `_stage_score(s)` | F + recomputed T (rsiScore + adxScore). Used for phase-tab top-3 sort. Matches scanner phase-tab display. |
| `_total_score(s)` | F + T + signal score. Used for all-tab top-3 sort. Matches scanner ALL tab display. |

### Triggered By
- EOD: `REFRESH_EOD.bat` / Task Scheduler at 3:35 PM (after orchestrator --eod)
- Intraday: kite_worker.py `--price-refresh` calls `_update_eval_prices()` (legacy inline code in kite_worker, not eval_worker --price-refresh)

---

## Part 5 — analytics_worker.py Reference

### Purpose
Reads `data/signals_log.json`, computes pre-aggregated analytics, writes `data/evals/analytics.json`.
Run after `eval_worker --eod` in the orchestrator pipeline.

### What It Computes
- **return_by_stage** — for each stage and `_all`: median, mean, alpha at checkpoints D7, D14, D21, D30, D45, D60
- **distribution_by_stage** — for each stage: total, wins, losses, best, worst at each checkpoint
- **win_rate_by_score_tier** — STRONG (≥41) / WATCH (28–40) / SKIP (<28) win rates
- **win_rate_by_vpb** — by vpbDetail (breakout / weak_breakout / vol_only / coiling / none)

---

## Part 6 — Server Endpoints (EVALS)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/evals` | Returns full `data/signals_log.json` |
| GET | `/api/evals/analytics` | Returns `data/evals/analytics.json` |
| GET | `/api/lifecycle` | Returns full `data/lifecycle_log.json` |
| GET | `/api/kite/open_browser` | Opens Kite login URL in server-side browser |
| POST | `/api/kite/auth` | Accepts request_token, completes Kite OAuth |

---

## Part 7 — EVALS Navigation Structure

EVALS is a separate section in the left nav with three independent panels:

| Nav Item | Panel | Data Source | Scope |
|----------|-------|-------------|-------|
| **Stock-Wise** | Day-by-day price matrix | `signals_log.json` | Signal-tier stocks only (~15–20 per EOD) |
| **Analytics** | Win rate charts, score tiers, equity curve | `signals_log.json` | Signal-tier stocks only |
| **Lifecycle** | Resolved/expired journeys, path index | `lifecycle_log.json` | ALL 2535 NSE stocks |

Each panel has its own route in `switchTab()` and fetches data independently. Lifecycle fetches `/api/lifecycle` and never uses `evSignals`. Stock-Wise and Analytics fetch `/api/evals`.

For detailed display rules per tab, see:
- `INSTRUCTIONS_FOR_STOCKWISE.md`
- `INSTRUCTIONS_FOR_LIFECYCLE.md`

---

## Part 8 — Analysis Goals

### Goal 1: Does our scanner strategy actually produce +15% gains?
- WIN rate across all stages tells us if the strategy has positive expectancy
- WIN rate by stage tells us which entry point is best (POST-CROSS vs BREAKOUT vs PULLBACK etc.)

### Goal 2: Does stage sequence matter?
- Lifecycle path index groups by full stage path — is `COIL → BRK → PRE-X → POST-X` better than `BRK → POST-X` directly?

### Goal 3: Which parameters best predict WIN?
- Analytics tab: win rate by score tier — does F+T ≥ 41 actually outperform F+T < 28?
- VPB detail: does BRK✓ (vpbScore ≥ 10) outperform BRK~ (vpbScore 5–6)?

### Goal 4: How long does it take to reach the target?
- Stock-Wise matrix: visual day-by-day progression
- Lifecycle Panel 1: AVG WIN DAY by entry phase

### Goal 5: What happens after WIN/LOSS?
- D0 reset after WIN creates a new cycle — the stock is re-tracked from the WIN price
- After LOSS (−9%), re-tracking answers: does it recover or continue down?

### Goal 6: Are EXPIRED stocks worth watching?
- Lifecycle Panel 2: EXPIRED stocks by path — some paths may produce consistent +8–12% gains (nearly WIN)
- AVG RET at D60 shows where expired stocks ended up

### Goal 7: NIFTY alpha
- Every signal tracks NIFTY price in parallel (`nifty_prices{}`)
- Alpha = signal_ret − nifty_ret at same trading day
- Positive alpha = outperformed the index regardless of market direction

---

## Part 9 — Legacy (To Be Removed When Confirmed Stable)

These EVALS functions remain in scanner workers from before `eval_worker.py` existed:
- `compute.py` → `log_signals()` (disabled/deprecated — eval_worker is now the sole logger)
- `kite_worker.py` → `_update_eval_prices()`, `_eval_compute_outcome()` (still active for intraday price updates; superseded by eval_worker for EOD logic but retained for intraday)

Do NOT remove `kite_worker._update_eval_prices()` — it handles intraday price patches to signals_log.json during `--price-refresh` cycles.
