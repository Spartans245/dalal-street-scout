# Dalal Street Scout — Metrics Reference

---

## Fundamentals

| Metric | Raw Data Required | Formula |
|---|---|---|
| **PE Ratio** ✅ | • PE Ratio | Sourced directly from NSE quote-equity (pdSymbolPe) — all 2736 stocks |
| **Sector Median PE** ✅ | • PE of all stocks in sector | median(PE) grouped by sector — excludes stocks with PE ≤ 0 (no earnings) — computed post-scan |
| **PE vs Sector** ✅ | • Stock PE • Sector Median PE | (stock PE − sector median PE) ÷ sector median PE × 100 — % premium/discount vs sector — F score tiers: 0 to +20% +12pts (sweet spot) / −20 to 0% +10pts / −40 to −20% +7pts / +20 to +40% +5pts / >+40% +2pts / −60 to −40% +2pts / <−60% 0pts (value trap) |
| **D/E Ratio** | • D/E Ratio | Sourced directly — not calculated |
| **ROE** | • ROE | Sourced directly — not calculated |
| **MCap** ✅ | • MCap | Sourced directly from NSE trade_info (totalMarketCap) — all 2736 stocks |

---

## Technical

| Metric | Raw Data Required | Formula |
|---|---|---|
| **RSI (14)** ✅ | • Daily Close (14+ days) | gain = Wilder smooth of positive daily changes (α=1/14); loss = Wilder smooth of negative daily changes (α=1/14); RSI = 100 − 100/(1 + gain/loss) |
| **EMA 14** ✅ | • Daily Close (14+ days) | Exponential moving average, span = 14 |
| **EMA 50** ✅ | • Daily Close (50+ days) | Exponential moving average, span = 50 |
| **EMA Cross** ✅ | • Daily Close (50+ days) | EMA14 crosses above EMA50 — detected within last 5 days |
| **EMA Pre-Cross** ✅ | • Daily Close (50+ days) | EMA14 below EMA50, gap < 0.5% of price, EMA14 rising faster than 3 days ago |
| **EMA Post-Cross** ✅ | • Daily Close (50+ days) | Cross happened 1–2 days ago AND EMA14–EMA50 gap still < 1.5% of price |
| **EMA Pullback** ✅ | • Daily Close (50+ days) | Cross happened ≥ 2 days ago AND price was > 2% above EMA14 in last 5 days (confirmed move away) AND price is now 0–2% above EMA14 (dipped back, still above) |
| **EMA Trend** ✅ | • Daily Close (50+ days) | EMA14 > EMA50, no fresh cross in last 5 days |
| **MACD Signal** ✅ | • Daily Close (26+ days) | MACD line = EMA(12) − EMA(26); Signal = EMA(MACD, 9); True if MACD crossed above Signal today |
| **Golden Cross** ✅ | • Daily Close (190+ days) | EMA30 > EMA190 |
| **ADX (14)** ✅ | • Daily High (28+ days) • Daily Low (28+ days) • Daily Close (28+ days) | TR = max(H−L, \|H−Cprev\|, \|L−Cprev\|); +DM / −DM per bar; Wilder smooth(14) → ATR, +DI, −DI; DX = 100 × \|+DI−−DI\| / (+DI+−DI); ADX = Wilder smooth(DX, 14) |
| **VPB — Price Coiling** ✅ | • Daily Close (6 days) | (max − min of last 5 closes) / min < 0.04 (range < 4%) |
| **VPB — Vol Shrinking** ✅ | • Daily Volume (25 days) | All of last 3 days volume < 85% of 20-day avg baseline |
| **VPB — Vol Ratio** ✅ | • Daily Volume (25 days) | Today volume ÷ avg20 baseline |
| **VPB — Close Position** ✅ | • Daily High • Daily Low • Daily Close | (Close − Low) ÷ (High − Low) |
| **VPB Score** ✅ | • Daily High, Low, Close, Volume (25+ days) | BRK✓ +10 (setup + vol≥2× + pos≥0.70) / BRK✓ +7 (setup + vol≥1.5× + pos≥0.60) / BRK~ +5 (setup + vol≥1.5× + pos<0.60) / VOL +4 (no setup + vol≥2× + pos≥0.70) / COIL✓ +3 (setup + vol<1×) / COIL~ +2 (price coiling only) / DIST −2 (setup + vol≥1.5× + pos<0.30) |
| **Vol Confirmed Cross** ✅ | • Daily Volume (50+ days) • Daily Close (50+ days) | Cross day volume ≥ 1.5× 20-day avg before cross |
| **Cross Score** ✅ | • Daily Volume (50+ days) • Daily Close (50+ days) | 18 (vol confirmed, 1–2d ago) / 14 (vol confirmed, 3–4d ago) / 10 (vol confirmed, 5d ago) / 8 (no vol confirmation) |
| **Near 38W High** ✅ | • Daily Close (193 days) | Close ≥ max(Close, 193d) × 0.92 — default (Kite depth ~38W) |
| **38W High / Low** ✅ | • Daily High / Low (193 days) | max(High, 193d) / min(Low, 193d) — from Kite history |
| **Near 52W High** | • Daily Close (252 days) | Close ≥ max(Close, 252d) × 0.92 — sourced from NSE, stored separately |
| **52W High / Low** | • Daily Close (252 days) | max(Close, 252d) / min(Close, 252d) — from NSE quote-equity (weekHighLow) |
| **ATH(9M)** ✅ | • Daily High (193 days) | max(High, 193d) — Kite history depth; default for now. ⚠ Decision pending: replace with true ATH when NSE data available |
| **MM Target** ✅ | • Daily High, Low (6 days) • Current Price | price + (max(High[−6:−1]) − min(Low[−6:−1])) |
| **Target Price** ✅ | • MM Target • 38W High • ATH(9M) • Current Price | Nearest of MM Target, 38W High, ATH(9M) that is still above current price |

---

## Liquidity

| Metric | Raw Data Required | Formula |
|---|---|---|
| **Avg 20 Vol** ✅ | • Daily Volume (25 days) | Mean of days −25 to −5 (excludes most recent 5 days so coiling period does not distort baseline) |
| **Daily Vol (₹ Cr)** ✅ | • Daily Volume (25 days) • Current Price | Avg 20 Vol × Price ÷ 10,000,000 |
| **Vol Ratio** ✅ | • Daily Volume (25 days) | Today volume ÷ Avg 20 Vol |
