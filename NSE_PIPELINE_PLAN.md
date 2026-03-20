# NSE Direct Data Pipeline — Architecture Plan

> **Rule #1: YF and NSE pipelines must NEVER share caches, state, or data.**
> The user switches sources via a UI toggle. Both pipelines produce identical stock dict schemas
> and the same UI renders both. D/E and ROE are shown as N/A for NSE (not available).

---

## Folder Structure

```
d:/Dalal_street/
│
├── server.py                    ← MODIFIED (~80 lines added only)
├── index.html                   ← MODIFIED (source toggle UI only)
│
├── cache.json                   ← YF cache (UNTOUCHED)
├── tickers_cache.json           ← YF ticker cache (UNTOUCHED)
│
├── shared/                      ← NEW — pure math, shared by both pipelines
│   ├── __init__.py
│   └── technicals.py            ← calc_technicals, classify_stage, score, calc_target, _safe_json
│
└── nse/                         ← NEW — entire NSE pipeline
    ├── __init__.py              ← Public API for server.py to import
    ├── session.py               ← NSE session manager (cookie renewal, rate limiting)
    ├── bhavcopy.py              ← Bhavcopy downloader + OHLCV history builder
    ├── fundamentals.py          ← quote-equity fetcher (PE, MCap, sector, 52W, name)
    ├── scanner.py               ← nse_fetch_all_stocks, nse_refresh_*, nse_scheduler
    ├── state.py                 ← NSE state dict + state_lock (mirrors YF state shape)
    ├── cache.py                 ← save/load for nse/cache.json and nse/tickers_cache.json
    │
    ├── cache.json               ← NSE stock cache (SEPARATE from root cache.json)
    ├── tickers_cache.json       ← NSE ticker cache (SEPARATE)
    ├── active_source.json       ← Persists 'yf'|'nse' across server restarts
    └── bhavcopy/                ← Daily Bhavcopy CSVs (rolling 260-day window)
        ├── 2026-03-19.csv.gz
        └── ...
```

---

## What Gets Extracted to `shared/technicals.py`

These functions are pure math on OHLCV data — no Yahoo, no NSE dependencies.
Copy verbatim from `server.py`, modify only the `score()` D/E sentinel check.

| Function | Purpose |
|---|---|
| `calc_technicals(hist)` | RSI, EMA signals, ADX (Wilder), VPB, near_52high |
| `classify_stage(tech)` | 7-stage priority classification |
| `score(pe, debtEq, roe, dvol, tech)` | F+T scoring — add `if debtEq is None: skip D/E tier` |
| `calc_target(price, mm, wk52h, ath)` | Nearest overhead target |
| `_safe_json(data)` | numpy-safe JSON serializer |

`server.py` then does `from shared.technicals import ...` and removes local copies.
`nse/scanner.py` does the same import. **One source of truth for all calculations.**

---

## NSE Data Sources

### What NSE Provides
| Metric | NSE Endpoint | Field |
|---|---|---|
| Price (live) | quote-equity | `lastPrice` |
| Prev close | quote-equity | `previousClose` |
| MCap | quote-equity | `issuedSize × lastPrice` |
| PE | quote-equity | `pdSymbolPe` |
| 52W High/Low | quote-equity | `weekHighLow.max / .min` |
| Sector/Name | quote-equity | `industryInfo.industryDesc / companyName` |
| Volume (today) | quote-equity | `totalTradedVolume` |
| OHLCV history | Bhavcopy CSV | all 2736 stocks in one daily file |

### What NSE Does NOT Provide (shown as N/A)
- **D/E ratio** — not in quote-equity
- **ROE** — not in NSE public API

### D/E Scoring Fix
`score()` gets `debtEq=None` from NSE scanner. Modified to:
```python
if debtEq is not None:
    if debtEq < 0.3:   f += 10
    elif debtEq < 0.7: f += 7
    ...
# debtEq=None → 0 points (unknown, not penalised, not rewarded)
```
Max score in NSE mode: **42** (52 − 10 D/E tier). UI shows "N/A" in D/E cell.

---

## NSE Session Management (`nse/session.py`)

NSE requires session cookies. Cookies expire in ~15 min.

```python
class NSESession:
    HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)...',
        'Referer': 'https://www.nseindia.com/',
        'X-Requested-With': 'XMLHttpRequest',
    }
    SESSION_TTL = 600  # renew every 10 min

    def _refresh(self):
        # GET homepage + market page to establish session cookie
        s = requests.Session()
        s.get('https://www.nseindia.com', headers=self.HEADERS, timeout=15)
        s.get('https://www.nseindia.com/market-data/live-equity-market', ...)
        self._session = s

    def get(self, url):
        # Auto-renew if stale; retry once if NSE returns HTML instead of JSON
        ...

nse_session = NSESession()  # module-level singleton, thread-safe
```

**Rate limiting:** 0.3s between quote-equity calls.
2736 stocks × 0.3s = **13.7 min** for a full fundamentals refresh.

---

## Bhavcopy OHLCV Strategy (`nse/bhavcopy.py`)

**Key insight:** One Bhavcopy file per day covers ALL 2736 stocks. No per-ticker history calls.

```
Bhavcopy URL: https://archives.nseindia.com/products/content/sec_bhavdata_full_{DDMONYYYY}.csv

One file = ~1.5 MB raw, ~200 KB gzipped
260 files (1 year) = ~52 MB on disk
Download time: 260 × 0.5s = ~2 min (one-time bootstrap only)
```

### Functions
- `ensure_history(n_days=260)` — downloads missing files, skips weekends/holidays, deletes old files
- `download_bhavcopy(date)` → `pd.DataFrame` with all stocks for that day
- `build_ohlcv(symbol, n_days=260)` → per-ticker DataFrame from disk cache
  - Reads cached .csv.gz files, filters for `SERIES == 'EQ'`, normalises column names
  - Returns DataFrame with DatetimeIndex, columns `[Open, High, Low, Close, Volume]`
  - Compatible with existing `calc_technicals(hist)` signature — **no changes to math**

### Full Scan Timing
1. `ensure_history(260)` — 2 min one-time bootstrap, then seconds/day after
2. `fetch_all_fundamentals()` — 13.7 min sequential quote-equity calls
3. Per-ticker: `build_ohlcv(sym)` (disk read) + `calc_technicals()` — seconds total
4. **Total: ~16 min** (vs 23+ min for YF, with better coverage)

---

## NSE Scanner Functions (`nse/scanner.py`)

```python
from shared.technicals import calc_technicals, classify_stage, score, calc_target
from nse.session import nse_session
from nse.bhavcopy import build_ohlcv, ensure_history
from nse.fundamentals import fetch_quote, fetch_all_fundamentals
from nse.state import nse_state, nse_state_lock

def nse_scan_one(symbol, quote_data, ohlcv) -> dict | None:
    # Assembles stock dict using NSE data
    # debtEq=None, roe=None explicitly
    # adds 'data_source': 'nse' field

def nse_fetch_all_stocks():
    # 1. ensure_history(260)
    # 2. get_nse_tickers() — reuse from server.py
    # 3. fetch_all_fundamentals() — sequential 0.3s/call
    # 4. For each ticker: build_ohlcv + nse_scan_one
    # 5. save_nse_cache()

def nse_refresh_prices():
    # Sequential quote-equity for each stock, 0.3s delay

def nse_refresh_technicals():
    # ensure_history() then build_ohlcv per stock — pure disk, very fast

def nse_refresh_fundamentals():
    # fetch_quote per stock, 0.3s delay

def nse_scheduler():
    # Mirrors server.py scheduler(), runs in daemon thread
    # Only started when user switches to NSE source
```

---

## State Separation

```
server.py globals:
  state        ← YF state dict (always present, YF scheduler always runs)
  state_lock

nse/state.py globals:
  nse_state    ← NSE state dict (initialized empty, NSE scheduler starts on first toggle)
  nse_state_lock
```

Both dicts have **identical schema** — same keys, same types. The HTTP handler uses:

```python
def _active_state():
    if active_source == 'nse':
        return nse_state, nse_state_lock
    return state, state_lock
```

All `/api/status`, `/api/stocks`, `/api/ctrl` endpoints call `_active_state()`.

---

## server.py Changes (Minimal)

**~80 lines added total. Zero existing lines deleted.**

### New additions:
1. `from shared.technicals import ...` at top (replaces local definitions)
2. `active_source` variable + `_active_state()` helper
3. `_read_active_source()` / `_write_active_source()` for persistence
4. Lazy NSE import: `_get_nse()` — only loaded when user first toggles to NSE
5. Two new routes: `/api/source` (GET) and `/api/set_source?source=yf|nse`
6. Existing routes modified to use `_active_state()` instead of `state` directly

### Routes that need `_active_state()`:
- `/api/status` — stock count, scan status, last updated
- `/api/stocks` — full stock list
- `/api/ctrl` — control panel metrics
- `/api/ctrl/run_full_scan`
- `/api/ctrl/run_technicals`
- `/api/ctrl/run_fundamentals`
- `/api/ctrl/run_prices`

---

## UI Toggle Design (index.html)

### Header toggle (pill button):
```html
<div class="source-toggle">
  <button id="btnYF"  class="src-btn active" onclick="setSource('yf')">YF</button>
  <button id="btnNSE" class="src-btn"        onclick="setSource('nse')">NSE</button>
</div>
```

### Source badge in mode strip:
```
● MARKET OPEN    NSE DIRECT    NIFTY 50: 23,456 (+0.4%)
```

### N/A display for D/E:
```javascript
// In table cell render:
s.debtEq === null ? 'N/A' : s.debtEq.toFixed(2)
```

### Toggle behaviour:
- On switch: calls `/api/set_source?source=nse`
- Server persists to `nse/active_source.json`
- Frontend reloads data from new source
- If NSE has no cache yet: shows "Full scan required" message with Run button

---

## Cache Naming Convention

| File | Source | Purpose |
|---|---|---|
| `cache.json` | YF | YF stock data (existing, unchanged) |
| `tickers_cache.json` | YF | YF ticker list (existing, unchanged) |
| `nse/cache.json` | NSE | NSE stock data (new) |
| `nse/tickers_cache.json` | NSE | NSE ticker list (new) |
| `nse/active_source.json` | Both | Persists toggle selection |
| `nse/bhavcopy/*.csv.gz` | NSE | Daily OHLCV files (260-day rolling window) |

---

## Implementation Sequence

### Phase 1 — Shared Math (zero risk)
1. Create `shared/technicals.py` — copy 5 functions from server.py
2. Add `debtEq is None` guard to `score()`
3. Modify `server.py` to import from shared — verify YF behaviour unchanged

### Phase 2 — NSE Session + Fundamentals
4. Create `nse/session.py` with `NSESession`
5. Create `nse/fundamentals.py` with `fetch_quote`
6. Test: `python -c "from nse.session import nse_session; r = nse_session.get('https://www.nseindia.com/api/quote-equity?symbol=RELIANCE'); print(r.json().get('lastPrice'))"`

### Phase 3 — Bhavcopy OHLCV
7. Create `nse/bhavcopy.py`
8. Test: download 5 days, build OHLCV for RELIANCE, compare RSI with YF output

### Phase 4 — NSE Scanner
9. Create `nse/state.py`, `nse/cache.py`, `nse/scanner.py`, `nse/__init__.py`
10. Run test scan on 10 tickers — verify stock dict schema matches YF

### Phase 5 — Server Integration
11. Add routing layer to `server.py`
12. Test toggle via curl/browser

### Phase 6 — UI
13. Add toggle button and source badge to `index.html`
14. Add N/A cell rendering

### Phase 7 — Validation
15. Full NSE scan end-to-end
16. Compare stage distributions with YF scan
17. Test cookie renewal during long scan
18. Test server restart with NSE as active source

---

## What This Achieves vs Current YF Pipeline

| | Yahoo Finance | NSE Direct |
|---|---|---|
| Stock coverage | ~1400–1450 (Yahoo's limit) | ~2736 (all NSE EQ stocks) |
| MCap reliability | Often 0 due to rate limiting | Always correct (issuedSize × price) |
| Rate limiting | Aggressive IP bans | No bans, 0.3s delay sufficient |
| PE data | Often missing | Available for listed stocks |
| D/E / ROE | Available (when not rate-limited) | **Not available (N/A)** |
| OHLCV method | Per-ticker yf.history() | Bhavcopy (all stocks in one daily file) |
| Full scan time | ~23 min + rate limiting | ~16 min, no rate limiting |
| Live price refresh | yf.download() batch (fast) | Sequential quote-equity (7 min for 1470) |
