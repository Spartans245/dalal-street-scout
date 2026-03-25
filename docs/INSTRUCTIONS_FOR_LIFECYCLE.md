# Lifecycle Analysis — Specification

**File:** `d:/Dalal_street/INSTRUCTIONS_FOR_LIFECYCLE.md`
**Created:** 2026-03-25
**Owner:** lifecycle_worker.py → data/lifecycle_log.json → /api/lifecycle → EVALS › Lifecycle tab

---

## Purpose

Track **what path a stock took** from its starting phase to its final outcome (+15% WIN or -9% LOSS or EXPIRED at 60 days). The lifecycle is purely about the **journey**, not the selection. Every one of the 2535 NSE-tracked stocks participates.

---

## Phase Index

Every phase is assigned a canonical number. The journey of a stock is expressed as an ordered sequence of these numbers.

| # | Phase Label | Code |
|---|-------------|------|
| 1 | COILING     | `coiling` |
| 2 | BREAKOUT    | `breakout` |
| 3 | PRE-CROSS   | `pre_cross` |
| 4 | POST-CROSS  | `post_cross` |
| 5 | PULLBACK    | `pullback` |
| 6 | TRENDING    | `trending` |
| 0 | NONE        | `none` (unclassified — recorded but not displayed as a phase step) |

**Path notation example:** `1→2→4` means the stock was in COILING, then moved to BREAKOUT, then to POST-CROSS before resolution.

A phase is only recorded **once** in the path sequence — if a stock revisits a phase it was already in, it is not added again (dedup preserving order of first visit).

---

## D0 — Start Date and Reset Logic

- **Global D0 start date:** 2026-03-24 (the day tracking began)
- Each stock has its own `d0` field — the date the current tracking cycle started
- D0 is the reference point for all day counts (D1, D2 … D60)

### D0 Reset Rules

| Event | New D0 |
|-------|--------|
| WIN (+15% hit) | outcome date (the day +15% was reached) |
| LOSS (-9% hit) | outcome date (the day -9% was breached) |
| EXPIRED (60 days elapsed, no target hit) | day 61 of previous cycle = new D0 |

After a reset, tracking restarts fresh — the path, price history, and phase log are cleared and recording begins from the new D0.

---

## Outcome Definitions

| Outcome | Condition |
|---------|-----------|
| WIN | Close price ≥ D0 close × 1.15 at any point within 60 calendar days of D0 |
| LOSS | Close price ≤ D0 close × 0.91 at any point within 60 calendar days of D0 |
| EXPIRED | Neither WIN nor LOSS triggered within 60 calendar days of D0 |
| OPEN | Fewer than 60 days elapsed and no target hit yet |

Win takes priority over Loss if both would trigger on the same day.

---

## Per-Stock Record (lifecycle_log.json)

File schema: `version: 2` (bumped when `d0_snapshot` was added).

```json
{
  "ticker": "RILINFRA",
  "d0": "2026-03-24",
  "d0_phase": "post_cross",
  "d0_price": 36.25,
  "d0_snapshot": { "...see D0 Snapshot fields below..." },
  "phase_log": [
    {"date": "2026-03-24", "phase": "post_cross", "day_n": 0},
    {"date": "2026-03-26", "phase": "pullback",   "day_n": 2},
    {"date": "2026-03-28", "phase": "trending",   "day_n": 4}
  ],
  "path": [4, 5, 6],
  "path_str": "4→5→6",
  "prices": {
    "2026-03-24": 36.25,
    "2026-03-25": 37.10
  },
  "outcome": "OPEN",
  "outcome_date": null,
  "outcome_day": null,
  "outcome_ret": null,
  "resolved_cycles": []
}
```

### Fields

| Field | Description |
|-------|-------------|
| `ticker` | NSE symbol |
| `d0` | Current cycle start date |
| `d0_phase` | Phase the stock was in on D0 |
| `d0_price` | Close price on D0 (baseline for +15%/-9% calculation) |
| `d0_snapshot` | Rich entry-point context captured once at D0 (see below) |
| `phase_log` | List of `{date, phase, day_n}` — one entry per phase change |
| `path` | Ordered list of phase numbers visited (deduped, first-visit order) |
| `path_str` | Human-readable path e.g. `"1→2→4"` |
| `prices` | Dict of `{date: close_price}` for the current cycle |
| `outcome` | `"OPEN"`, `"WIN"`, `"LOSS"`, `"EXPIRED"` |
| `outcome_date` | Date outcome was determined (null if OPEN) |
| `outcome_day` | Day number (0-based) when outcome occurred (null if OPEN) |
| `outcome_ret` | % return at outcome ((outcome_price - d0_price) / d0_price × 100) |
| `resolved_cycles` | List of completed cycle records (see below) |

---

## D0 Snapshot (`d0_snapshot`)

Captured once at cycle start from `stocks.json`. Never changes mid-cycle. Carried into `resolved_cycles` so past cycles are fully self-contained. Written by `make_snapshot(stock)` in `lifecycle_worker.py`.

### Technicals
| Field | Source field | Description |
|-------|-------------|-------------|
| `rsi` | `rsi` | RSI(14) |
| `adx` | `adx` | ADX |
| `ema14` | `ema14` | 14-day EMA |
| `ema50` | `ema50` | 50-day EMA |
| `ema_gap_pct` | `emaGapPct` | (ema14 − ema50) / price % — negative means below |
| `ema14_rising` | `ema14Rising` | True if EMA14 trending upward |
| `vol_ratio` | `volRatio` | Today's volume ÷ 20-day avg volume |
| `close_pos` | `closePos` | (close − low) / (high − low) — candle position |
| `avg20_vol` | `avg20Vol` | 20-day average volume |
| `price_coiling` | `priceCoiling` | Range < 4% over 5 days |
| `vol_shrinking` | `volShrinking` | Volume < 85% of avg over 3 days |

### EMA Signals
| Field | Source field | Description |
|-------|-------------|-------------|
| `ema_cross` | `emaCross` | True if EMA14 crossed EMA50 within last 5 days |
| `ema_cross_days` | `emaCrossDays` | Days since cross |
| `ema_pre_cross` | `emaPreCross` | True if gap < 0.5% and EMA14 rising fast (cross imminent) |
| `ema_post_cross` | `emaPostCross` | True if crossed 1–2d ago, lines still < 1.5% apart |
| `ema_pullback` | `emaPullback` | True if price within 2% of EMA14 after cross |
| `vol_confirm` | `volConfirm` | True if volume ≥ 1.5× avg on cross day |
| `cross_score` | `crossScore` | EMA cross strength: 18/14/10/8 |

### VPB Signal
| Field | Source field | Description |
|-------|-------------|-------------|
| `vpb_score` | `vpbScore` | VPB score (0–10) |
| `vpb_detail` | `vpbDetail` | `breakout` / `weak_breakout` / `vol_only` / `coiling` / `none` |

### Scores
| Field | Source field | Description |
|-------|-------------|-------------|
| `f_score` | `fScore` | Fundamentals score (max 30) |
| `t_score` | `tScore` | Technical score = rsiScore + adxScore (max 22) |
| `score` | `score` | Total stored score (F+T or F+T+S depending on context) |

### Fundamentals
| Field | Source field | Description |
|-------|-------------|-------------|
| `pe` | `pe` | P/E ratio |
| `debt_eq` | `debtEq` | Debt-to-equity ratio |
| `roe` | `roe` | Return on equity % |
| `promoter` | `promoterHolding` | Promoter holding % |
| `pledging` | `pledging` | Pledged shares % |
| `de_flag` | `deFlag` | Debt tier: `DEBT FREE` / `LOW DEBT` / `MODERATE` / `HIGH DEBT` / `LEVERAGED` |

### Market Context
| Field | Source field | Description |
|-------|-------------|-------------|
| `mcap` | `mcap` | Market cap in Cr |
| `sector` | `sector` | Sector name |
| `sector_median_pe` | `sectorMedianPe` | Median P/E for the sector |
| `pe_vs_sector` | `peVsSector` | % over/under sector median P/E |

### Price Context
| Field | Source field | Description |
|-------|-------------|-------------|
| `wk52_high` | `wk52High` | 52-week high |
| `wk52_low` | `wk52Low` | 52-week low |
| `wk38_high` | `wk38High` | 38-week high |
| `wk38_low` | `wk38Low` | 38-week low |
| `pct_from_38high` | `pctFrom38High` | % below 38W high (negative = below) |
| `pct_from_38low` | `pctFrom38Low` | % above 38W low |
| `near_52high` | `near52High` | True if within 5% of 52W high |
| `near_38high` | `near38High` | True if within 5% of 38W high |
| `upside_pct` | `upsidePct` | % upside to target price |
| `target_price` | `targetPrice` | Computed target price |
| `target_type` | `targetType` | Target basis: `"52W"` / `"ATH9M"` / `"38W"` |

### Support & Resistance
| Field | Source field | Description |
|-------|-------------|-------------|
| `sr_resistance` | `srResistance` | List of `{price, strength}` — resistance levels above price (max 5) |
| `sr_support` | `srSupport` | List of `{price, strength}` — support levels below price (max 5) |

### Volume & Liquidity
| Field | Source field | Description |
|-------|-------------|-------------|
| `daily_vol_cr` | `dailyVol` | Daily turnover in Cr |

---

### Resolved Cycle Record (archived on D0 reset)

```json
{
  "d0": "2026-03-24",
  "d0_phase": "post_cross",
  "d0_price": 36.25,
  "d0_snapshot": { "...full snapshot at entry..." },
  "path": [4, 5, 6],
  "path_str": "4→5→6",
  "outcome": "WIN",
  "outcome_date": "2026-04-15",
  "outcome_day": 22,
  "outcome_ret": 16.3
}
```

The `d0_snapshot` is carried verbatim from the live record into `resolved_cycles` so that analytics can access all entry conditions for past cycles without needing to reconstruct them.

---

## Three UI Panels (Lifecycle Tab)

### When panels populate

Panels are **empty on day one**. They start filling only as stocks resolve — i.e., when a stock hits +15% (WIN), hits −9% (LOSS), or reaches day 60 without either (EXPIRED). There is no "Active" panel because active stocks have nothing interesting to show yet — their journey is not complete. The data worth displaying is the completed journey.

---

### Panel 1 — Resolved Journeys (WIN / LOSS)

Stocks that hit their +15% or −9% target within 60 days. This is the most important panel.

**Columns:** TICKER | D0 DATE | D0 PHASE | PATH | WIN / LOSS | ACTUAL DATE | DAY N | RET%

**Sort:** most recent resolution first (newest at top)

**Shows:** Whether WIN or LOSS, the exact date it resolved, which day in the cycle (D1–D60), and the return achieved. Color-coded: green row for WIN, red row for LOSS.

---

### Panel 2 — Expired Journeys

Stocks that completed a full 60-day cycle without hitting +15% or −9%.

**Columns:** TICKER | D0 DATE | D0 PHASE | PATH | DAYS | FINAL RET% | EXPIRED

**Sort:** most recent expiry first (newest at top)

**Shows:** The full path the stock took, what phase it started in, where it ended up, and the final return at day 60.

---

### Panel 3 — Stage Path Index

An aggregated view showing which paths are most common and most effective.

**Rows:** Each unique path string (e.g. `1→2→4`, `4→5`) that has appeared in resolved or expired journeys.

**Columns:** PATH | COUNT | WIN | LOSS | EXPIRED | WIN RATE | AVG WIN DAY | AVG RET%

**Sort:** count desc (most travelled paths at top)

**Purpose:** Answers "which stage sequences actually lead to wins?" — pure data, no filtering needed.

---

## lifecycle_worker.py — Responsibilities

**Trigger:** Same EOD batch as eval_worker (3:35 PM Mon–Fri via Task Scheduler / REFRESH_EOD.bat)

**Input:** `data/computed/stocks.json` (today's scanner output — stage + close price for all 2535 stocks)

**Output:** `data/lifecycle_log.json`

**Algorithm (per stock, each EOD run):**

```
1. Load existing record from lifecycle_log.json (or create new with d0 = today)
2. Compute day_n = (today - d0).days
3. Record today's close price into prices dict
4. Record today's phase into phase_log if phase changed since last entry
5. Update path / path_str from phase_log (deduped first-visit sequence)
6. Check WIN: any price in current cycle >= d0_price * 1.15 → outcome = WIN
7. Check LOSS: any price in current cycle <= d0_price * 0.91 → outcome = LOSS
8. Check EXPIRED: day_n >= 60 and outcome == OPEN → outcome = EXPIRED
9. If outcome is WIN / LOSS / EXPIRED:
     a. Archive current cycle to resolved_cycles
     b. Reset: d0 = outcome_date (WIN/LOSS) or today (EXPIRED), clear prices/phase_log/path
10. Save back to lifecycle_log.json
```

**Server endpoint:** `GET /api/lifecycle` → returns `lifecycle_log.json`

---

## Separation from eval_worker.py — STRICT

| Concern | eval_worker.py | lifecycle_worker.py |
|---------|---------------|---------------------|
| Scope | Signal-tier stocks only (top 3 per phase daily) | All 2535 NSE stocks |
| Data file | `data/signals_log.json` | `data/lifecycle_log.json` |
| API endpoint | `/api/evals` | `/api/lifecycle` |
| UI tabs | Stock-Wise tab, Analytics tab | Lifecycle tab ONLY |
| Phase selection | Yes (top 3 sort logic applied) | No — records whatever phase scanner shows |
| Tier field | `signal` / `baseline` | Not applicable — no tiers |

**Absolute rules:**
- `lifecycle_worker.py` NEVER reads from `signals_log.json`
- `eval_worker.py` NEVER reads from `lifecycle_log.json`
- Both independently read `data/computed/stocks.json` as their input
- Stock-Wise tab and Analytics tab are NEVER modified by lifecycle changes
- Lifecycle tab is NEVER affected by signal-tier selection logic from eval_worker

---

## Key Decisions

1. **Phase 0 (NONE) in path:** NONE phases are logged in `phase_log` for completeness but excluded from `path` / `path_str` display — paths only show named phases 1–6.
2. **Same-day multiple phase changes:** Only the end-of-day phase is recorded (one entry per date in phase_log).
3. **Win > Loss priority:** If a stock somehow hits both +15% and -9% on the same day (unlikely), WIN is recorded.
4. **D0 price:** Always the close price on D0 date as recorded in stocks.json that day.
5. **Resolved cycles are retained:** `resolved_cycles` accumulates all past cycles for that ticker, allowing long-term path analysis.
