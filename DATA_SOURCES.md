# Dalal Street Scout — Data Source Analysis

## Metrics Required by Scanner

| Metric | Raw Data Needed | Used For |
|---|---|---|
| RSI | Daily OHLCV (14+ days) | T score, stage classification |
| ADX | Daily OHLCV (14+ days) | T score |
| EMA 14 / EMA 50 | Daily OHLCV (50+ days) | EMA cross, pre-cross, pullback, trending stages |
| VPB Setup & Trigger | Daily OHLCV + Volume (20+ days) | Breakout/coiling stage, VPB score |
| ATH (5yr) | Daily OHLCV (5 years) | Max upside / target price calculation |
| Max Upside / MM Target | Daily OHLCV + 52W High | Upside % display, sorting |
| Live price / change % | Last traded price | Price refresh every 5 min during market hours |
| Daily volume (₹ Cr) | Volume × price (20d avg) | Liquidity filter, VPB trigger |
| 52W High / Low | 1 year OHLCV or direct field | near_52high badge, target calc |
| PE ratio | EPS TTM + price, or direct | F score (up to 20 pts) |
| D/E ratio | Total Debt + Shareholders Equity | F score (up to 10 pts) |
| ROE | Net Income + Shareholders Equity | Display only (not scored) |
| MCap | Shares outstanding × price | MCap filter tabs, tiebreak sort |
| Sector | Company metadata | Sector filter dropdown |
| Company name | Company metadata | Display |
| Ticker universe | List of all NSE symbols | Scan universe |

---

## Data Source Comparison

### Coverage

| Metric | Yahoo Finance | NSE Direct API | Kite Connect |
|---|---|---|---|
| RSI (OHLCV) | ⚠️ ~1400/2736 stocks | ✅ All 2736 | ✅ All stocks (paid) |
| ADX (OHLCV) | ⚠️ ~1400/2736 stocks | ✅ All 2736 | ✅ All stocks (paid) |
| EMA 14/50 (OHLCV) | ⚠️ ~1400/2736 stocks | ✅ All 2736 | ✅ All stocks (paid) |
| VPB (OHLCV + Volume) | ⚠️ ~1400/2736 stocks | ✅ All 2736 | ✅ All stocks (paid) |
| ATH — 5yr OHLCV | ⚠️ ~1400/2736 stocks | ✅ All 2736 | ✅ All stocks (paid) |
| Max Upside | ⚠️ ~1400/2736 stocks | ✅ All 2736 | ✅ All stocks (paid) |
| Live price / change % | ⚠️ Batch, rate limited | ✅ All 2736 via quote-equity | ✅ All stocks, ltp() / WebSocket |
| Daily volume | ⚠️ ~1400/2736 stocks | ✅ All 2736 | ✅ All stocks (paid) |
| 52W High / Low | ✅ info field | ✅ weekHighLow in quote-equity | ✅ Compute from 1y OHLCV |
| PE ratio | ✅ info['trailingPE'] ~1400 | ✅ pdSymbolPe all 2736 | ❌ Not available |
| D/E ratio | ✅ info['debtToEquity'] ~1400 | ❌ Not available | ❌ Not available |
| ROE | ✅ info['returnOnEquity'] ~1400 | ❌ Not available | ❌ Not available |
| MCap | ⚠️ info['marketCap'] unreliable | ✅ issuedSize × price, all 2736 | ⚠️ Compute from instruments |
| Sector | ✅ info['sector'] ~1400 | ✅ industryInfo.sector all 2736 | ❌ Not available |
| Company name | ✅ info['longName'] ~1400 | ✅ companyName all 2736 | ❌ Not available |
| Ticker universe | ❌ No NSE list | ✅ EQUITY_L.csv, all 2736 | ✅ instruments('NSE') |

---

### Rate Limiting & Reliability

| Aspect | Yahoo Finance | NSE Direct API | Kite Connect |
|---|---|---|---|
| Official API | ❌ Unofficial scraping | ❌ Unofficial, browser simulation | ✅ Official paid API |
| Documented rate limits | None published | None published | 3 req/s historical data |
| Practical safe rate | ~2 req/s for .info | ~3–5 req/s | 3 req/s |
| Batch support | ✅ yf.download(50 tickers) = 1 req | ❌ 1 req per stock always | ✅ Multiple instruments per call |
| Auth / session needed | ❌ None | ✅ Cookie session, expires ~10–15 min | ✅ Daily login token refresh |
| IP blocking | Throttles, returns incomplete data | ✅ Actively blocks IPs | ❌ Token-based, no IP block |
| Fails silently | ✅ Returns empty dict, no error | ⚠️ Returns HTML instead of JSON | ❌ Explicit error codes |
| Market hours stability | Stable | ⚠️ Slow/unreliable 9:15–15:30 | ✅ Stable |
| Endpoint stability | Changes occasionally | ⚠️ Changes without notice | ✅ Versioned API |
| 2736 stocks full scan @ 0.5s | ~23 min, mostly works | ~23 min + cookie refresh complexity | ~15 min, reliable |
| Live price 2736 stocks / 5 min | ⚠️ Rate limited | ❌ Near-certain IP block | ✅ WebSocket, all stocks real-time |
| Cost | Free | Free | ₹2000/month |

---

## Gap Analysis — What No Free Source Provides

| Missing | Impact | Options |
|---|---|---|
| D/E ratio for ~1336 stocks | F score partial for half the universe | Yahoo as best-effort fallback, or drop D/E |
| ROE for ~1336 stocks | Display only, no score impact | Yahoo as best-effort fallback, or drop |
| Live prices for all 2736 every 5 min | Intraday price refresh incomplete | NSE Bhavcopy (EOD only) or accept Yahoo's ~1400 |

---

## Recommended Hybrid Architecture

| Data | Source | Frequency | Notes |
|---|---|---|---|
| Ticker universe | NSE EQUITY_L.csv | Monthly | Free, complete |
| OHLCV history (5y) | NSE Historical API | Monthly full scan | Covers all 2736, sequential with delay |
| OHLCV history (1y) | NSE Historical API | Daily (morning) | Covers all 2736 |
| Live price / volume | Yahoo Finance batch download | Every 5 min (market hours) | ~1400 stocks, no .info calls, lenient |
| PE, MCap, 52W, Sector, Name | NSE quote-equity API | Weekly | All 2736, one call per stock |
| D/E, ROE | Yahoo Finance .info | Weekly | Best-effort ~1400 stocks, not blocking |

### Why this split works
- NSE Historical API is lenient for OHLCV (date-range queries, not real-time)
- Yahoo batch download for prices only — no .info, much less rate limiting
- NSE quote-equity for fundamentals covers 2736 vs Yahoo's 1400
- D/E/ROE from Yahoo as optional enrichment — stocks without it score 0, not excluded

---

## NSE Direct API — Key Endpoints

| Endpoint | Data Returned |
|---|---|
| `https://archives.nseindia.com/content/equities/EQUITY_L.csv` | Full ticker universe |
| `https://www.nseindia.com/api/historical/cm/equity?symbol=X&series=EQ&from=DD-MM-YYYY&to=DD-MM-YYYY` | Daily OHLCV for one stock |
| `https://www.nseindia.com/api/quote-equity?symbol=X` | Live price, PE, MCap, 52W high/low, sector, name |
| `https://www.nseindia.com/api/quote-equity?symbol=X&section=trade_info` | Order book, volume |

### NSE quote-equity fields relevant to scanner
```
lastPrice        → current price
change / pChange → price change
pdSymbolPe       → PE ratio
weekHighLow.max  → 52W High
weekHighLow.min  → 52W Low
issuedSize       → shares outstanding (× price = MCap)
industryInfo.sector → sector
companyName      → company name
isSuspended      → skip suspended stocks
```

---

## Decision Pending
- Whether to replace Yahoo OHLCV with NSE Historical API (covers 2736 vs 1400)
- Whether to drop D/E from F score entirely (no free source covers all 2736)
- Whether live price refresh stays on Yahoo batch or moves to NSE quote-equity with proper session handling
