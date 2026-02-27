# Dalal Street Scout — Complete Documentation
*Last updated: Feb 2026*

---

## What This Application Does

A personal NSE stock screener that:
- Scans all NSE-listed stocks filtered to **₹100 Cr – ₹10,000 Cr market cap**
- Scores every stock out of 100 using a **breakout-focused strategy**
- Shows **live prices during market hours** (auto-refresh every 5 min)
- Saves data to **cache.json** so restarts are instant (no re-scanning)
- Runs entirely on your **own computer** — no cloud, no subscriptions, no API keys

---

## Files — What Each One Does

```
your-folder/
├── START_SERVER.bat   ← Double-click this to start (Windows)
├── start_server.sh    ← Run this on Mac
├── server.py          ← The brain — fetches data, scores stocks, serves API
├── index.html         ← The UI — open in browser at http://localhost:5000
└── cache.json         ← Auto-created after first scan — DO NOT DELETE
```

---

## How to Run

1. Double-click `START_SERVER.bat`
2. First time: waits ~45–60 min for full NSE scan → saves `cache.json`
3. Every time after: loads `cache.json` in ~5 seconds → browser opens instantly
4. Open browser at **http://localhost:5000**
5. Close the terminal window to stop the server

---

## Auto-Start on Windows Boot (Recommended)

So you never have to manually start it:

1. Press `Win + R` → type `taskschd.msc` → Enter
2. Click "Create Basic Task"
3. Name: `Dalal Street Scout`
4. Trigger: "When the computer starts"
5. Action: "Start a program"
6. Program: full path to `START_SERVER.bat` e.g. `C:\Users\RAJARSHI\Desktop\scout\START_SERVER.bat`
7. Finish

From now on — server starts automatically every morning when you boot up.

---

## Architecture — How It Works

```
Internet (Yahoo Finance / NSE)
         ↓  fetches every 5 min during market hours
    server.py  (runs on localhost:5000)
         ↓  stores in RAM + cache.json
    index.html (browser polls /api/status every 8 seconds)
```

### API Endpoints (server.py serves these)
| Endpoint | What it returns |
|---|---|
| `GET /` | Serves index.html |
| `GET /api/status` | Progress, market mode, stock count |
| `GET /api/stocks` | All stock data (scores, prices, technicals) |
| `GET /api/prices` | Prices only (for quick refresh) |
| `GET /api/stock/RVNL` | Single stock detail with chart data |
| `GET /api/rescan` | Triggers a full re-scan in background |

### Market Modes
| Time (IST) | Mode | What happens |
|---|---|---|
| 9:15 AM – 3:30 PM | OPEN | Prices refresh every 5 min via Yahoo Finance |
| After 3:30 PM | EOD | Cache saved, plan locked for tomorrow |
| Before 9:15 AM | PRE | Uses cached data |
| Saturday/Sunday | WEEKEND | Uses cached data |

---

## Scoring System — 100 Points Total

### Fundamentals — 35 pts
| Rule | Points | Notes |
|---|---|---|
| No promoter pledging | 8 pts | Assumed 0 — **verify manually on screener.in** |
| P/E < 15 | 12 pts | |
| P/E 15–25 | 9 pts | |
| P/E 25–35 | 5 pts | |
| P/E 35–50 | 2 pts | |
| D/E < 0.3 | 10 pts | |
| D/E 0.3–0.7 | 7 pts | |
| D/E 0.7–1.0 | 4 pts | |
| D/E 1.0–1.5 | 1 pt | |
| Daily Vol > ₹10 Cr | 5 pts | |
| Daily Vol > ₹5 Cr | 3 pts | |
| Daily Vol > ₹2 Cr | 1 pt | |

**ROE is NOT scored** — shown as a warning badge only:
- 🟢 ROE > 20% — strong
- 🟡 ROE 12–20% — acceptable
- 🔴 ROE < 12% — weak (warning shown in detail view)

### Technicals — 40 pts (breakout-focused)
| Signal | Points | Logic |
|---|---|---|
| 🔀 14 EMA crosses 50 EMA upward (last 3 days) | 12 pts | Strongest signal — breakout starting |
| 📈 14 EMA above 50 EMA + rising (trend) | 7 pts | Continuation entry |
| RSI 45–58 (coiling zone) | 12 pts | Pre-breakout sweet spot |
| RSI 58–65 (breaking out) | 7 pts | Acceptable but slightly extended |
| RSI > 65 | 0 pts | Already ran — don't chase |
| ADX 20–35 (trend starting) | 10 pts | Trend building but not exhausted |
| 📦 Consolidating (price range < 6% over 15 days) | 5 pts | Coiled spring |
| 🎯 Within 8% of 52-week high | 3 pts | At breakout level |
| Volume contract → expand | 6 pts | Classic breakout volume pattern |
| MACD crossover | 2 pts | Supplementary confirmation only |
| Golden Cross (30/200 EMA) | 1 pt | Bonus |

### Catalyst — 25 pts
Currently 0 — manual tagging only (news API not yet integrated).
Future: auto-detect order wins, earnings beats, institutional buying.

### Liquidity — 10 pts
| Rule | Points |
|---|---|
| Daily vol > ₹5 Cr | 10 pts |
| Daily vol > ₹2 Cr | 5 pts |
| Daily vol > ₹0.5 Cr | 2 pts |

### Thresholds
- **65+ = Strong Entry** (act on these)
- **45–64 = Watch** (monitor, wait for confirmation)
- **< 45 = Skip**

---

## Technical Indicators — How They're Calculated

All calculated from 1-year daily OHLCV data fetched from Yahoo Finance.

### RSI (Relative Strength Index)
```python
delta = daily price changes
avg_gain = rolling 14-day mean of positive changes
avg_loss = rolling 14-day mean of negative changes
RSI = 100 - (100 / (1 + avg_gain/avg_loss))
```
Sweet spot for breakout entry: **45–58** (coiling, not overbought)

### 14/50 EMA Cross
```python
EMA14 = exponential moving average of last 14 days
EMA50 = exponential moving average of last 50 days

CROSS = EMA14 crossed above EMA50 in last 3 days  → strongest signal
TREND = EMA14 already above EMA50 + both rising   → continuation
```
**Why 14/50 instead of 30/200 (Golden Cross)?**
14/50 fires 3–4 weeks earlier than 30/200 — catches the move at start,
not after 15% has already happened.

### ADX (Trend Strength)
```python
# Uses ATR (Average True Range) as denominator
ATR = avg of (high-low, |high-prev_close|, |low-prev_close|) over 14 days
directional_move = |price_now - price_10days_ago| / (ATR * 10)
ADX = directional_move * 25  (capped 5–50)
```
Sweet spot: **20–35** (trend forming but not exhausted)

### Consolidation Detection
```python
recent_15_days = closing prices last 15 days
range_pct = (max - min) / min * 100
consolidating = range_pct < 6.0%
```
Tight range = compressed energy = breakout coming

### Volume Pattern
```python
avg_20 = average volume last 20 days
avg_5  = average volume last 5 days
vol_contract = avg_5 < avg_20 * 0.80   # volume drying up
vol_expand   = today > avg_20 * 1.25   # today spiking
```
Contract then expand = classic pre-breakout setup

---

## Cache System

### How it works
```
Startup flow:
  cache.json exists AND < 24 hours old?
    YES → load instantly (5 sec) → refresh prices in background
    NO  → full scan (~45-60 min) → save cache.json

Auto-save:
  After every full scan
  Every day when market closes at 3:30 PM

cache.json contains:
  - All stock data (price, fundamentals, technicals, scores, 60-day chart)
  - Timestamp of when it was saved
  - ~300-500 stocks depending on what passes MCap filter
```

### Force Rescan
Click **"⟳ Force Full Rescan"** button (red, top-right of browser) when:
- You just updated server.py (new scoring logic)
- Cache is stale or corrupted
- You want completely fresh fundamentals (quarterly)

Current data stays visible while rescan runs in background.

---

## Stock Universe

### Source
Downloads official NSE equity list from:
`https://archives.nseindia.com/content/equities/EQUITY_L.csv`

Falls back to a hardcoded list of ~400 small/mid cap stocks if NSE blocks the download.

### Filter
- MCap minimum: **₹100 Cr**
- MCap maximum: **₹10,000 Cr**
- Typically 300–600 stocks pass this filter depending on market conditions

---

## UI Features

### Scanner Tab
- Filter by min score, sector, signal type
- Click any row → full detail modal
- **⚗ Strategy Playground** button → slide-out panel to test different scoring rules live
- NEW SCORE column shows impact of playground changes

### Tomorrow's Plan Tab
- Top 6 picks by score
- Entry price, partial exit (+15%), full target (+27.5%), stop loss (-9%)
- Pre-market checklist

### Watchlist Tab
- Add any NSE ticker
- Click row → full detail modal with 60-day price chart
- **"Open on Screener.in"** button for promoter pledging check

### Changes Tab
- Compares current scan to previous — shows what entered/left strong zone
- Score improvements and drops

### Strategy Playground (slide-out from Scanner)
- Toggle any scoring rule on/off
- Scanner re-ranks live as you toggle
- Shows original vs new score for every stock
- Summary: how many gained/lost 70+ threshold

---

## Investment Strategy

### Entry Criteria
- MCap ₹100 Cr – ₹10,000 Cr
- Score 65+
- P/E below sector average
- Zero promoter pledging (verify on screener.in — NOT auto-detected)
- D/E below 1.0
- ROE above 15% (warning only, not hard filter)
- Minimum daily volume ₹5 Cr
- Catalyst present (order win, earnings beat, institutional buying)

### Exit Rules
- Book **50% at +15%**
- Trailing stop loss **7–8% from peak** on remaining
- Full exit at **+30%**
- Hard stop loss **–9% from entry** — no exceptions
- Exit if catalyst retracted
- Exit if no 10% move in 6 weeks
- Reassess at 3 months

### Avoid
- Stocks up 40–50% in 1 month with no news (already ran)
- Upcoming QIP / FPO (dilution)
- Increasing promoter pledging
- Daily volume below ₹5 Cr

---

## Known Limitations

1. **Promoter pledging** — Yahoo Finance free API doesn't provide this.
   Always check manually on **screener.in** before trading.

2. **Catalyst scoring = 0** — News/order win detection not yet built.
   Catalyst score is manual only. Add manually via watchlist notes.

3. **ADX is approximate** — True ADX needs +DI/-DI calculation which
   requires proper OHLC processing. Our version is a directional momentum proxy.
   Good enough for swing trading signals but not identical to TradingView ADX.

4. **Yahoo Finance rate limits** — If too many stocks fail during scan,
   Yahoo may be rate-limiting. Server auto-pauses between batches.
   Full scan may take longer on slow connections.

5. **cache.json is not encrypted** — Contains stock data, not personal/financial data.
   No passwords or trading credentials stored anywhere.

---

## How to Modify / Add Features

### Change MCap filter
In `server.py`, lines near top:
```python
MCAP_MIN_CR = 100    # change minimum
MCAP_MAX_CR = 10000  # change maximum
```
Then Force Rescan.

### Change scoring weights
In `server.py`, find the `score()` function.
Each section is clearly commented with point values.
After changing, restart server + Force Rescan.

### Change refresh interval
In `server.py`:
```python
LIVE_REFRESH = 5 * 60  # seconds — change 5 to any number of minutes
```

### Change cache expiry
```python
CACHE_MAX_AGE_HOURS = 24  # change to e.g. 12 for twice-daily rescans
```

### Add a new tab to the UI
In `index.html`:
1. Add tab button in `.tabs` div
2. Add panel div with matching id `tab-yourname`
3. Add case in `switchTab()` if needed

---

## Troubleshooting

| Problem | Fix |
|---|---|
| "Python not found" | Install Python from python.org — check "Add to PATH" |
| "pip not recognized" | Re-install Python, check "Add to PATH" |
| Browser shows "Cannot connect to server" | Double-click START_SERVER.bat |
| Stuck on scanning for hours | Check terminal for errors — Yahoo Finance may be rate-limiting |
| Prices look wrong | Click Force Full Rescan |
| cache.json loads but scores seem off | You updated server.py — click Force Full Rescan |
| Port 5000 already in use | Another app using port 5000 — change `PORT = 5000` to `PORT = 5001` in server.py and update `const API = 'http://localhost:5001/api'` in index.html |

---

## Asking Claude to Continue Development

If starting a new Claude session, paste this at the start:

> "I have a stock screener application called Dalal Street Scout. 
> It's a local Python server (server.py) + HTML frontend (index.html).
> The server runs on localhost:5000, fetches NSE stocks via yfinance,
> filters by MCap ₹100–10,000 Cr, scores stocks out of 100 using a 
> breakout strategy (14/50 EMA cross, RSI 45-58, consolidation, volume pattern).
> Cache saves to cache.json. Read the README.md for full details."

Then describe what you want to change. Attach README.md and the relevant file.

---

## Changelog

| Date | Change |
|---|---|
| Session 1 | Basic HTML screener with simulated data |
| Session 2 | Real data via yfinance Python script |
| Session 3 | Local server architecture — server.py + index.html |
| Session 4 | Cache system — instant startup after first scan |
| Session 4 | Watchlist detail modal with 60-day price chart |
| Session 4 | Strategy Playground slide-out panel |
| Session 4 | Force Rescan button |
| Session 5 | New scoring engine — breakout-focused |
| Session 5 | 14/50 EMA cross/trend signal replaces 30/200 Golden Cross |
| Session 5 | RSI sweet spot changed to 45-58 (pre-breakout coil) |
| Session 5 | ROE removed from scoring — warning badge only |
| Session 5 | Added: consolidation detection, near-52W-high, volume pattern |
