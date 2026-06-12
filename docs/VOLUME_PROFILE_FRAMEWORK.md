# Volume Profile Trading System — Master Framework
Consolidated & Corrected | June 2026

## How to use this document
Read it top to bottom once. The sections build on each other.
Section order is deliberate — strategies are defined before candle rules so every
reference to "Strategy 1A" etc. has already been explained.

---

# PART 1 — FOUNDATIONS

## 1. Core Philosophy
Volume Profile shows WHERE volume was traded at each price level — not when.
It reveals institutional positioning, fair value zones, and high-probability entry levels.

Three charts. Three jobs. Never mixed.

| Chart  | Job             | What to look for                    |
|--------|-----------------|--------------------------------------|
| Weekly | Levels only     | VAH, POC, VAL, HVNs, LVNs            |
| 4H     | Entry trigger   | Live candle at level — fires first   |
| Daily  | Validation only | Confirms 4H entry was correct at EOD |

## 2. Key Terms

| Term | Full form          | Definition                                          |
|------|---------------------|------------------------------------------------------|
| VP   | Volume Profile      | Volume traded at each price level                    |
| POC  | Point of Control    | Price with highest volume — longest bar              |
| VAH  | Value Area High     | Top of 70% volume zone — GREEN line                  |
| VAL  | Value Area Low      | Bottom of 70% volume zone — BROWN line               |
| VA   | Value Area          | Price range containing 70% of all volume             |
| HVN  | High Volume Node    | Long bar zone — price slows and respects here        |
| LVN  | Low Volume Node     | Short bar zone — price moves fast through here       |

## 3. The 70% Rule
The Value Area contains 70% of all traded volume — this is the market's definition of fair value.

- Above VAH = market considers price TOO EXPENSIVE
- Inside VA = fair value — price accepted here
- Below VAL = market considers price TOO CHEAP

Bar colours on the profile:
- GREEN bars = inside value area (price accepted)
- BLUE bars = outside value area (price rejected)
- LONG bars = HVN — strong support or resistance
- SHORT bars = LVN — weak zone, price accelerates through

## 4. Chart Settings

Recommended setup:

| Chart  | Timeframe | History       | VP setting               |
|--------|-----------|----------------|---------------------------|
| Weekly | 1W        | 2–3 years      | Visible Range VP — ON     |
| Daily  | 1D        | 6–12 months    | NO VP — candles only      |
| 4H     | 4H        | 3–6 months     | NO VP — candles only      |

VP settings:
- Value Area % = 70 (never change)
- Extend POC Lines = ON
- Extend VA Lines = ON
- Chart type = Histogram

---

# PART 2 — THE TWO DIRECTIONS

Every setup belongs to one of two directions. Define this first before anything else.

## 5. Bounce vs Reclaim — Defined Once

**BOUNCE (Strategies 1A and 1C):**
- Price came from TOP → fell DOWN → is now AT the level
- = sellers brought price here
- = buyers must now defend it
- = price is already at optimal entry
- = no retest step needed

**RECLAIM (Strategies 1B, 1D, and 2):**
- Price came from BOTTOM → rose UP → crossed THROUGH the level
- = buyers pushed price above resistance
- = level must now flip from resistance to support
- = price moved away from optimal entry
- = must wait for price to return (retest) before entering

### Why the entry mechanics differ
For a bounce, price is at the level right now. Entering on the 4H candle that
defends it is already at the best possible price. Stop is tight.

For a reclaim, the reclaim candle moved price 200–500 points above the level.
Entering there means a wide stop, poor risk-reward. Waiting for the retest
brings price back to the level so the stop can be tight again.

```
Reclaim — entering on reclaim candle directly:
→ entry  = 7,200  (where reclaim candle closed)
→ stop   = below reclaim candle low = 6,900
→ risk   = 300 points
→ RR fails ❌

Reclaim — waiting for retest to level:
→ entry  = 7,160  (4H retest candle above level)
→ stop   = below 4H retest candle low = 7,100
→ risk   = 60 points
→ same target = RR 5:1 ✅
```

Retest is purely RR optimisation. It is not about reconfirming whether the level is valid.

---

# PART 3 — THE SIX STRATEGIES

## 6. Strategy 1A — POC / HVN Bounce (top down)
**When:** Price falls to a weekly POC or HVN from above.

```
WEEKLY  →  Identify weekly POC or HVN level
           Price is falling toward it from above

4H      →  Watch for 4H candle AT the level during market hours
           Candle touches level (wick or body)
           Candle closes ABOVE level
           Volume above 20-day average
           = ENTRY TRIGGER — enter above 4H candle high
           Stop = below 4H candle low

DAILY   →  At end of day: daily candle closes above level + volume
           = validates your 4H entry was correct
           You are already in the trade

TARGET  →  Next weekly HVN above
           Then weekly VAH
```

**Speed:** Fast — price at level already. 4H candle is the only trigger.
No retest step. No waiting for daily first.

## 7. Strategy 1B — POC / HVN Reclaim (bottom up)
**When:** Price rises from below and breaks through a weekly POC or HVN.

```
WEEKLY  →  Identify weekly POC or HVN
           Price is rising toward it from below

DAILY   →  Watch for reclaim candle:
           Green candle closes ABOVE POC/HVN
           Volume above 20-day average
           = Reclaim confirmed — DO NOT enter yet
           (entering here = wide stop = poor RR)

WAIT    →  Price pulls back toward POC/HVN from above
           Level must now hold as support

4H      →  Watch for retest candle at POC/HVN:
           Candle touches level
           Candle closes ABOVE level
           Volume above 20-day average
           = ENTRY TRIGGER — enter above 4H retest candle high
           Stop = below 4H retest candle low

DAILY   →  At end of day: daily candle at retest closes above level
           = validates your 4H entry was correct

TARGET  →  Next weekly HVN above
           Then weekly VAH
```

**Retest expiry:** If price does not return to the reclaimed level within
5 trading sessions after the reclaim candle, the setup is void.
Do not chase. Monitor for the next weekly level above.

## 8. Strategy 1C — VAL Bounce (top down)
**When:** Price falls to the weekly VAL from above.

```
WEEKLY  →  Identify weekly VAL level
           Price falling toward it from above

4H      →  Watch for 4H candle AT VAL during market hours
           Candle touches VAL (wick or body)
           Candle closes ABOVE VAL
           Volume above 20-day average
           = ENTRY TRIGGER — enter above 4H candle high
           Stop = below 4H candle low

DAILY   →  At end of day: validates 4H entry was correct

TARGET  →  Weekly POC (first target)
           Then weekly VAH
```

**Speed:** Fast — same as 1A. No retest step.

## 9. Strategy 1D — VAL Reclaim (bottom up)
**When:** Price was below weekly VAL, rises and breaks above it.

```
WEEKLY  →  Identify weekly VAL level
           Price rising toward it from below

DAILY   →  Watch for reclaim candle:
           Green candle closes ABOVE VAL
           Volume above 20-day average
           = Reclaim confirmed — DO NOT enter yet

WAIT    →  Price pulls back toward VAL from above
           VAL must now hold as support

4H      →  Watch for retest candle at VAL:
           Candle touches VAL
           Candle closes ABOVE VAL
           Volume above 20-day average
           = ENTRY TRIGGER — enter above 4H retest candle high
           Stop = below 4H retest candle low

DAILY   →  At end of day: validates 4H entry was correct

TARGET  →  Weekly POC (first target)
           Then weekly VAH
```

**Retest expiry:** Same as 1B — 5 sessions maximum. If no retest, void the setup.

## 10. Strategy 2 — VAH Breakout (momentum)
**When:** Price breaks above the weekly VAH with conviction.

```
WEEKLY  →  Identify weekly VAH
           Price approaching from below inside VA

DAILY   →  Watch for breakout candle:
           Strong green candle closes ABOVE VAH
           Body closes in top 25% of candle range
           Volume = minimum 2x the 20-day average (mandatory — see note)
           = Breakout confirmed — DO NOT enter yet

WAIT    →  Price pulls back toward VAH from above
           VAH must now hold as support

4H      →  Watch for retest candle at VAH:
           Candle touches VAH
           Candle closes ABOVE VAH
           Volume above 20-day average
           = ENTRY TRIGGER — enter above 4H retest candle high
           Stop = below 4H retest candle low

DAILY   →  At end of day: validates 4H entry was correct

TARGET  →  ATH (first target)
           VA projection = VAH + (VAH − VAL)
           New weekly VAH after value shift confirms
```

### Why 2x volume is mandatory for Strategy 2 only
VAH is the hardest level in the profile to break — it is the upper boundary of
where 70% of volume was accepted. A standard above-average volume can be a
temporary push. Only overwhelming institutional volume (2x+) gives the breakout
enough conviction to hold above VAH and not immediately reverse.

Strategies 1A–1D use "above average" because those levels are inside the profile
where institutional presence is already established.

### False breakout signals — do not enter
- Red candle closing above VAH = rejection, not breakout
- Green candle with volume below 2x average = wait for more
- Intraday pierce above VAH but daily closes below = not a breakout

### Retest expiry
No time limit applies after a VAH breakout. Price in the blue zone can run for
weeks before retesting. Wait as long as price stays within 20% of VAH.

### 20% extension rule
If price extends more than 20% above VAH without retesting, the original VAH
retest setup is void. At that point, plot Fixed Range VP on the post-breakout
range, identify the new weekly POC forming above old VAH, and apply Strategy 1A
bounce rules at the new POC. Old VAH remains valid as a deeper secondary support
level only.

### New HVN during run
If price consolidates above VAH and a visible HVN forms on the weekly VP before
any retest occurs, that HVN becomes the new retest candidate. Apply Strategy 1A
bounce rules at that HVN. Old VAH remains valid as deeper support only.

## 11. Strategy 3 — Weekly Level Confluence
**When:** Two or more weekly VP features cluster at the same price (within 25 points).

```
VALID CONFLUENCE COMBINATIONS (all from weekly chart only):
Weekly POC + Weekly HVN at same price
Weekly VAL + Weekly HVN at same price
Weekly VAH + Weekly HVN at same price
Two or more weekly HVNs stacked at same price

= Double or triple weekly confluence
= Highest confidence setup (85%+)
= Maximum position size

Entry rules: identical to Strategy 1A
Difference: higher conviction because institutional defence
is doubled or tripled at the same level
```

**CRITICAL:** Daily and 4H VP levels play NO role in Strategy 3.
They are obstacles, never confluence triggers.
Confluence is defined exclusively by weekly VP features.

## 12. Strategy Reference Table

| Strategy | Direction  | Level                          | Candle signal (daily)             | Volume rule              | Speed     | Target                          |
|----------|------------|----------------------------------|-------------------------------------|---------------------------|-----------|----------------------------------|
| 1A       | Top down   | POC / HVN                       | Any candle close above + vol        | Above average             | Fast      | Next weekly HVN, then VAH        |
| 1B       | Bottom up  | POC / HVN                       | Green close above + vol             | Above average              | Moderate  | Next weekly HVN, then VAH        |
| 1C       | Top down   | VAL                              | Any candle close above + vol        | Above average              | Fast      | Weekly POC, then VAH             |
| 1D       | Bottom up  | VAL                              | Green close above + vol             | Above average              | Moderate  | Weekly POC, then VAH             |
| 2        | Bottom up  | VAH                              | Strong green close above            | 2x average (hard)          | Moderate  | ATH, VA projection               |
| 3        | Either     | 2+ weekly features clustered    | Any candle close above + vol        | Above average              | Any       | Next weekly HVN                  |

---

# PART 4 — CANDLE RULES

## 13. Value Shift — What It Is and When It Confirms
Value Shift is not a standalone strategy. It is the confirmed result of a sustained VAH breakout.

```
Stage 1:  VAH breakout (Strategy 2) — price closes above VAH
Stage 2:  Multiple daily candles hold above VAH
Stage 3:  New daily and weekly POC forms above old VAH
Stage 4:  Old VAH becomes new VAL
          = VALUE SHIFT CONFIRMED
```

**Entry after value shift confirms:**
- Wait for pullback to old VAH (now new support)
- 4H candle at old VAH touches and closes above
- Volume above average
- Enter above 4H candle high, stop below 4H candle low
- Target = new weekly VAH

## 14. POC Break — Critical Exit Rule

```
POC BREAKS FROM ABOVE (price falls and daily closes below POC):
= EXIT IMMEDIATELY — no waiting, no averaging down
= Sellers took control at the most important level
= Wait for weekly VAL — look for Strategy 1C or 1D there

POC BREAKS FROM BELOW (price rises through POC):
= Strategy 1B applies
= Wait for daily confirmation candle above POC
= Enter on 4H retest of POC
= Target next weekly HVN above
```

---

# PART 4 (cont.) — CANDLE RULES

## 15. The Three Candle Types

Every entry signal involves one of three candle types depending on the direction and stage.

### Candle Type 1 — BOUNCE Candle
**Used in:** Strategy 1A, Strategy 1C

**What is happening:**
Price fell into the level from above. Buyers are stepping in to defend it.

**What is required:**
- ✅ Candle interacts with the level from above (wick or body touches)
- ✅ Candle closes ABOVE the level
- ✅ Volume above 20-day average

**Shape:** Not required.
Hammer adds confidence but any close above + volume = valid signal.

**Confidence levels:**
- Hammer wick tests level exactly = highest confidence
- Big body closing well above level = high confidence
- Small body closing just above level = lower confidence — valid but watch closely

### Candle Type 2 — RECLAIM Candle
**Used in:** Strategies 1B, 1D, 2 (the first candle — do not enter on this one)

**What is happening:**
Price is rising from below through resistance. Buyers pushing above the level with conviction.

**What is required:**
- ✅ Candle crosses through the level from below
- ✅ Candle closes ABOVE the level
- ✅ Volume above 20-day average (2x for Strategy 2 VAH breakout)

**Note:** Body size is the primary confidence indicator here, not shape.
Price is moving upward — there is no downward rejection happening.
Hammer and engulfing patterns are not relevant. Body size is.

**Confidence levels:**
- Big green body closing well above = highest confidence
- Medium body = moderate confidence
- Small body closing just above = lower confidence ⚠️ consider waiting

**Hard stop:** Never enter on the reclaim candle directly.
Wide stop → poor RR → see Part 2 for the maths. Always wait for the retest.

### Candle Type 3 — RETEST Candle
**Used in:** Strategies 1B, 1D, 2 (the entry candle after price pulls back)

**What is happening:**
After the reclaim candle pushed price above the level, price has now pulled back
to test whether the level held as support.

**What is required:**
- ✅ Candle touches the level from above (wick or body)
- ✅ Candle closes ABOVE the level
- ✅ Volume above 20-day average

**Shape:** Completely irrelevant.
The level's validity was already proven by the reclaim candle.
This candle only needs to close above the level with volume.
Hammer adds confidence only — it is not a requirement.

**Confidence levels:**
- Hammer wick testing level = higher confidence
- Any candle closing above = valid entry trigger
- Doji closing above = lower confidence but still valid

## 16. Shape Relevance Summary

| Candle type | Shape required? | What actually matters       | Confidence boost            |
|-------------|------------------|------------------------------|-------------------------------|
| Bounce      | No               | Close above level + volume   | Hammer wick at level          |
| Reclaim     | No               | Close above level + volume   | Large body size                |
| Retest      | No               | Close above level + volume   | Hammer wick at level          |

Hammer / Engulfing / Morning Star = confidence indicators only.
They are visual aids, not entry gates. Never required for any candle type.

**Note on the confirmation patterns (Hammer, Bullish Engulfing, Morning Star):**
These three shapes, when they appear at the right level, are the highest-confidence
versions of a valid candle. Use them to size up a position. Do not use their
absence as a reason to skip an otherwise valid signal (close above + volume).

## 17. The Universal Signal — Three Rules That Never Change

Regardless of strategy, candle type, or market — these three things define a valid signal:

```
RULE 1:  Candle interacts with the level
         → Bounces: wick or body touches from above
         → Reclaims: candle body crosses through from below
         → Retests: wick or body touches from above after reclaim

RULE 2:  Candle closes ABOVE the level

RULE 3:  Volume is above the 20-day average
         (2x average for Strategy 2 VAH breakout only)
```

All three present = valid signal.
Everything else (shape, pattern name, candle colour) = confidence adjustment only.

## 18. The Correct Timeframe Hierarchy

```
WEEKLY   →  tells you WHERE the level is (the only source of levels)
4H       →  tells you WHEN to enter (fires during market hours)
DAILY    →  tells you your entry was correct (validates at end of day)
```

The 4H fires before the daily closes. This is by design, not a problem.

**WRONG (old understanding):**
```
Weekly identifies level
→ Wait for daily to close above level
→ Then use 4H for retest
= will miss fast bounce moves entirely
```

**CORRECT:**
```
Weekly identifies level
→ Watch 4H at level during market hours
→ 4H candle closes above level + volume = enter now
→ Daily close at end of day confirms your entry
→ You are already in the trade with a tight stop
```

**Why bounces cannot wait for the daily:**
```
Day 1:  Daily closes above level
Day 2:  Price is already 200 points above level
Day 3:  Price is 400 points above level
        = retest to level never came
        = waited for daily = missed entire move

4H solution:
During Day 1 market hours → 4H candle at level closes above + volume
→ Enter immediately
→ Daily close that evening validates the entry
→ Position is running
```

## 19. What Daily Validation Actually Means

The daily candle at end of day answers one question:
**"Did buyers specifically defend this level today?"**

```
Daily closes ABOVE level + volume  =  YES ✅
                                    Buyers defended with conviction
                                    Institutional money confirmed present

Daily closes BELOW level           =  NO ❌
                                    Sellers still in control
                                    If not yet in trade: do not enter
                                    If already in 4H entry: red flag —
                                    tighten stop to the level itself or
                                    close partial. Full exit only if
                                    next daily also closes below.

Daily closes AT level              =  INDECISION ⚠️
                                    Neither side won
                                    Wait for next candle to close clearly above
                                    Do not enter until indecision resolves
```

### After-hours planning — the daily is the planning chart, the 4H is the live trigger
The 4H is a LIVE-HOURS tool. It only fires while the market is open. When reviewing
a stock after the close to plan for tomorrow, the 4H has nothing to offer — no candle
will form tonight. The daily is the correct planning chart.

```
TONIGHT (after close, daily chart):
→ Where is price vs the weekly levels?
→ Is price AT a weekly level (within 0.5%)?
→ Did today's daily candle close above/below that level + volume?
→ If a setup is forming, write the IF-THEN for tomorrow

TOMORROW (live, 4H chart):
→ Execute the planned trigger on the 4H during market hours
```

Example IF-THEN written tonight:
> "Price closed at weekly POC today on good volume — IF a 4H candle tomorrow holds
> above POC with volume, enter above its high, stop below its low, T1 = VAH."

The daily decides WHETHER to be ready. The 4H decides the EXACT entry tomorrow.
Do not convert a strong daily close into a market-open buy order that skips the 4H —
a great daily close can gap-fail the next morning. Keep the order conditional on the
4H confirmation, or be ready to cancel if the 4H does not confirm.

## 20. Multi-Candle Sequences at the Level

**Single candle — always the strongest signal:**
```
One candle touches level
Closes well above level with a large body
Volume spike
= Strongest possible signal
= No second candle needed
= Move to 4H entry immediately
```

**Two candles — valid under one condition:**

SCENARIO A — Both days close above (valid):
```
Day 1: touches level, closes above ✅
Day 2: closes above level (does not need to touch) ✅
= Day 1 tested the level, Day 2 confirmed hold
= Valid two-candle confirmation
= Slightly lower conviction than a single strong candle
= Watch for 4H trigger if one has not already fired
```

SCENARIO B — Day 1 below, Day 2 above (invalid):
```
Day 1: touches level, closes BELOW ❌ — sellers won day 1
Day 2: closes above level
= Day 1 failure is not erased by Day 2
= NOT valid
= Reset — wait for a clean single candle to start again
```

## 21. Hammer — Where It Is Relevant and Where It Is Not

**HAMMER RELEVANT:**

→ Bounce candle (1A / 1C):
  Price fell into level. Wick tests level from below candle body.
  Shows the fight happened exactly at the level.
  Buyers rejected sellers at the precise price.
  Highest confidence version of a bounce candle.

→ Retest candle (1B / 1D / 2):
  Price pulled back to level after reclaim.
  Wick tests level, body closes above.
  Confirms level flipped from resistance to support.
  Adds confidence to the retest entry.

**HAMMER NOT RELEVANT:**

→ Reclaim candle (1B / 1D / 2):
  Price moving upward through the level.
  There is no downward rejection at this candle.
  Hammer shape has no meaning here.
  Body SIZE is the indicator for reclaim — not shape.

## 22. Volume — Non-Negotiable

```
Volume above 20-day average          = institutional conviction ✅
Volume below 20-day average          = retail noise ⚠️  — do not trade it
```

**Volume thresholds by strategy:**
- Bounce (1A, 1C): above 20-day average
- Reclaim (1B, 1D): above 20-day average
- Retest (1B, 1D, 2): above 20-day average
- VAH Breakout (Strategy 2): minimum 2x the 20-day average

No volume = no trade, regardless of candle shape or pattern.
High volume + close above level = the core signal. Always.

### The 20-period average is timeframe-specific — do not read across charts
"Above 20-day average" means a different denominator on each chart:
- On the DAILY chart → measured against the last 20 DAILY candles
- On the 4H chart → measured against the last 20 4H candles

Same rule, different reference set. A 4H candle's volume is judged against average
4H volume; a daily candle's against average daily volume. Never read the volume bar
off one timeframe and apply it to the other. This is why a 4H trigger can fire on
above-average 4H volume while the daily that evening still closes below the level —
the two are measuring different windows, and that is the daily flagging that intraday
strength did not hold into the close (see §19).

---

# PART 5 — EXECUTION

## 23. Entry Rules

**Universal entry sequence — all strategies:**

```
STEP 1 — WEEKLY CHART:
Identify which weekly level price is approaching.
Determine direction: coming from above (bounce) or below (reclaim).
Determine which strategy applies (1A / 1B / 1C / 1D / 2).

STEP 2 — 4H CHART (during market hours):
Watch for 4H candle at the weekly level.
Bounce:  4H candle touches level, closes above, volume above average → enter
Reclaim: wait for retest 4H candle after reclaim candle confirmed → enter
Entry = above 4H signal candle high
Stop  = below 4H signal candle low (see §24)

STEP 3 — DAILY CHART (end of day):
Daily close confirms or questions the entry.
Already in trade at this point — daily is validation, not a gate.
```

**Candle signal by strategy:**

| Strategy | 4H entry candle description                                  |
|----------|----------------------------------------------------------------|
| 1A, 1C   | Bounce: touches level, closes above, volume above average      |
| 1B, 1D   | Retest: pulls back to level, closes above, volume above avg    |
| 2        | Retest: pulls back to VAH, closes above, volume above avg      |
| 3        | Same as 1A — bounce candle at confluent level                  |

## 24. Stop Loss Rules

**BOUNCE entry (1A, 1C):**
```
Stop = below the LOW of the 4H entry candle
       (the candle that touched the level and closed above it)
       Level is the floor — if it breaks, trade is wrong
```

**RECLAIM RETEST entry (1B, 1D, 2):**
```
Stop = below the LOW of the 4H retest candle
       (the candle that pulled back to the level and closed above it)
       Level just proved support — if it breaks, trade is wrong
```

**Universal stop rules:**
- Never use VP levels as stop loss
- Never use arbitrary round numbers as stop
- Stop is always defined by the signal candle's low
- If stop distance creates RR below 2:1 — do not take the trade

## 25. Target Rules

All targets come from the weekly chart only.

```
INSIDE VALUE AREA:
T1 = next weekly HVN above entry
T2 = next weekly HVN after that
T3 = weekly VAH

AT VAH BREAKOUT (Strategy 2):
T1 = ATH
T2 = VA projection (VAH + (VAH − VAL))
T3 = new weekly VAH after value shift confirms
```

**Booking strategy:**
```
Book 30% at T1 → move stop to breakeven
Book 30% at T2 → move stop to T1
Book 20% at T3 → move stop to T2
Trail remaining 20% toward final target
```

### Stop placement after T1 — trail to the booked level, not just breakeven
"Move stop to breakeven" is the minimum after the first partial. The tighter and
usually better placement is just BELOW the level you just booked at.

Trade bought at weekly VAL, T1 = weekly POC:
```
Price hits POC → book 30%

Option A (conservative): stop to breakeven (VAL entry)
  → on a reversal, the runner rides all the way back to VAL before exiting
  → gives back the entire POC-to-VAL move

Option B (tighter, preferred): stop to just below POC (the booked level)
  → on a reversal, the runner exits just under POC
  → keeps most of the move, exits near the high
```

Option B is the §25 ladder applied correctly — once price is past a weekly level,
the stop rides up behind it. This also removes any temptation to use a drifting
daily VAH as a mid-range exit: a stop below the booked weekly level gets you out
higher than a daily VAH exit would, and it uses a durable locked level instead of
a recalculating one. Pick one rule (A or B) and apply it every trade — never decide
per-trade in the moment.

Daily and 4H levels are obstacles to monitor — never targets.

## 26. Failed Bounce — Second Touch Rules

```
First bounce entry is stopped out.
Price is now back at the same weekly level again.

SECOND TOUCH — valid re-entry conditions:
→ Treat as a fresh bounce setup
→ Requirements are HIGHER than the first attempt:
   Larger candle body than the first attempt
   Higher volume than the first attempt
   Preferably a hammer (wick testing the level precisely)
→ If these higher conditions are met: valid re-entry

TWO FAILED TOUCHES — do not re-enter:
→ Level has been tested twice and failed both times
→ Selling pressure is overcoming buying at this level
→ Do not enter a third time at this level
→ Wait for price to reach weekly VAL
→ Look for Strategy 1C or 1D at VAL instead
```

---

# PART 6 — DECISION TOOLS

## 27. Master Decision Tree

```
WHERE IS PRICE RELATIVE TO WEEKLY LEVELS?
│
├── BELOW weekly VAL
│   └── Wait for daily green candle closing above VAL + volume
│       → Strategy 1D (VAL Reclaim)
│       → Wait for 4H retest of VAL from above
│       → Enter above 4H retest candle high
│       → Target: weekly POC first, then VAH
│
├── AT weekly VAL (price approaching from above)
│   └── Watch for 4H candle at VAL during market hours
│       Touches VAL, closes above, volume above average
│       → Strategy 1C (VAL Bounce)
│       → Enter above 4H candle high
│       → Target: weekly POC first, then VAH
│
├── AT weekly POC / HVN (price approaching from above)
│   └── Watch for 4H candle at level during market hours
│       Touches level, closes above, volume above average
│       → Strategy 1A (POC/HVN Bounce)
│       → Enter above 4H candle high
│       → Target: next weekly HVN above, then VAH
│
├── AT weekly POC / HVN (price approaching from below)
│   └── Wait for daily green candle closing above level + volume
│       = Reclaim confirmed
│       → Strategy 1B (POC/HVN Reclaim)
│       → Wait for 4H retest of level from above (max 5 sessions)
│       → Enter above 4H retest candle high
│       → Target: next weekly HVN above
│
├── BETWEEN weekly levels
│   ├── Within 0.5% of nearest weekly level
│   │   └── Treat as AT the level — strategy rules apply normally
│   │       Check direction (bounce or reclaim)
│   │       Confirm 4H candle + volume
│   │       Confirm RR ≥ 2:1 to next weekly level
│   │       Example: POC at 430, price at 431 = at POC, valid setup
│   │
│   └── Beyond 0.5% from any weekly level — TRUE no man's land
│       └── DO NOTHING
│           No institutional reference point
│           No setup exists
│           Wait for price to reach a weekly level
│
├── APPROACHING weekly VAH from below
│   └── Wait for daily green candle closing above VAH
│       Volume = minimum 2x average (mandatory)
│       = Breakout confirmed
│       → Strategy 2 (VAH Breakout)
│       → Wait for 4H retest of VAH from above (max 5 sessions)
│       → Enter above 4H retest candle high
│       → Target: ATH, then VA projection
│
├── ABOVE weekly VAH — blue zone (sustained)
│   └── Monitor for value shift:
│       New POC forming above old VAH?
│       Multiple daily candles holding above?
│       → Value shift forming (see §13)
│       → Pullback to old VAH = 4H entry
│       → Target: new weekly VAH
│
├── POC breaks FROM ABOVE (daily closes below POC)
│   └── EXIT IMMEDIATELY
│       Do not average down
│       Wait for weekly VAL
│       → Strategy 1C or 1D at VAL
│
└── POC breaks FROM BELOW (daily closes above POC after being below)
    └── Strategy 1B applies
        Wait for 4H retest of POC
        Enter above 4H retest candle high
```

## 28. Fixed Range VP — When to Use It

Plot Fixed Range VP only when one of these four events occurs:

```
TRIGGER 1: Weekly candle closes above weekly VAH
           Compare pre-breakout period vs post-breakout
           Check whether new POC is forming above old VAH

TRIGGER 2: Weekly candle closes below weekly VAL
           Compare pre-breakdown vs post-breakdown
           Check whether new POC forming below old VAL

TRIGGER 3: Major swing high or swing low forms
           Compare rally phase vs correction phase VP

TRIGGER 4: Major news or results event
           Compare pre-event vs post-event VP
           Look for volume shift at new price levels
```

## 29. Confidence Levels and Position Sizing

| Setup quality                                  | Confidence | Position size |
|--------------------------------------------------|------------|-----------------|
| Single timeframe signal only                      | 65%        | Half size       |
| 4H entry confirmed by same-day daily close        | 75%        | Normal size     |
| Triple POC confluence (Strategy 3)                | 85%        | Full size       |
| Triple POC confluence + daily candle              | 90%+       | Maximum size    |

## 30. Stock Analysis Checklist

**STEP 1 — Weekly chart (2–3 year view):**
- [ ] Mark VAH, POC, VAL
- [ ] Mark all HVN zones (long bars)
- [ ] Mark all LVN zones (short bars)
- [ ] Where is current price relative to levels?
- [ ] What is the nearest weekly level?
- [ ] What is the target weekly level?
- [ ] Is the target 10–15%+ away? If not — skip this stock.

**STEP 2 — Determine direction:**
- [ ] Is price approaching the level from above? → Bounce (1A or 1C)
- [ ] Is price approaching the level from below? → Reclaim (1B, 1D, or 2)

**STEP 3 — Daily chart (6–12 month view, no VP):**
- [ ] Has a valid daily candle formed at the weekly level?
  - Closes above level ✅
  - Volume above 20-day average ✅
  - (Hammer / engulfing adds confidence but not required)
- [ ] If YES and bounce → proceed to 4H
- [ ] If YES and reclaim → wait for retest, then proceed to 4H
- [ ] If NO → wait

**STEP 4 — 4H chart (3–6 month view, no VP):**
- [ ] Is price at or pulling back to the weekly level?
- [ ] Has a 4H signal candle formed?
  - Touches level ✅
  - Closes above level ✅
  - Volume above 20-day average ✅
- [ ] Note the exact candle HIGH → entry price
- [ ] Note the exact candle LOW → stop loss price

**STEP 5 — Calculate RR:**
- [ ] Entry = above 4H signal candle high
- [ ] Stop = below 4H signal candle low
- [ ] Risk = entry price − stop price
- [ ] T1 = next weekly HVN above
- [ ] T2 = weekly VAH (or ATH for Strategy 2)
- [ ] RR = (T1 − entry) / risk
- [ ] RR ≥ 2:1? If not — skip this trade.

**STEP 6 — Execute:**
- [ ] Place limit or market entry order
- [ ] Place stop loss immediately — before anything else
- [ ] Set price alerts at T1, T2, T3
- [ ] Plan partial booking at each target (30 / 30 / 20 / trail 20)

---

# PART 7 — MASTER RULES

## 31. The Non-Negotiable Rules — Complete List

**LEVELS**
- RULE 1: All levels come from weekly VP only
- RULE 2: Daily and 4H VP = completely ignore
- RULE 3: Daily and 4H price levels = obstacles only, never targets
- RULE 4: True no man's land = do nothing, no setup exists
- RULE 4A: "At a level" means within 0.5% of the weekly level — entry allowed.
  Example: weekly POC at 430 → anywhere from 427.85 to 432.15 = at the level
- RULE 4B: Near a level (within 0.5%) + valid 4H candle + RR ≥ 2:1 = valid entry.
  Conviction comes from the proximity to the level and the clear target above
- RULE 4C: Beyond 0.5% from any weekly level with no clear proximity = do nothing

**CANDLE SIGNALS**
- RULE 5: Shape is never a hard gate — close above + volume = always sufficient
- RULE 6: Hammer / engulfing / morning star = confidence boost only, never required
- RULE 7: Hammer is relevant at bounce and retest candles only
- RULE 8: Hammer is irrelevant at reclaim candles — body size matters instead
- RULE 9: Close AT the level = indecision = do not enter
- RULE 10: Volume is mandatory for every candle type, every strategy

**TIMEFRAME HIERARCHY**
- RULE 11: 4H candle is the entry trigger — it fires during market hours
- RULE 12: Daily candle validates the entry at end of day — it does not precede the entry
- RULE 13: Never enter on 4H signal from a level that is not a weekly VP level

**BOUNCE RULES**
- RULE 14: Bounce = no retest step needed — 4H candle at level is the entry
- RULE 15: Stop for bounce = below the low of the 4H entry candle

**RECLAIM RULES**
- RULE 16: Never enter on the reclaim candle directly — stop too wide, RR fails
- RULE 17: Reclaim requires retest before entry — purely for RR optimisation
- RULE 18: Retest must occur within 5 sessions — otherwise setup is void
- RULE 19: Stop for reclaim retest = below the low of the 4H retest candle

**VAH BREAKOUT RULES**
- RULE 20: VAH breakout requires minimum 2x average volume — non-negotiable
- RULE 21: Red candle above VAH = rejection, never a breakout signal
- RULE 22: Intraday VAH pierce with daily close below = not a breakout

**EXECUTION RULES**
- RULE 23: Minimum RR = 2:1. Below this — skip the trade, no exceptions
- RULE 24: Stop loss is placed immediately at the time of entry — never after
- RULE 25: All targets come from weekly chart levels only
- RULE 26: Never place a target inside an LVN — price moves fast through them
- RULE 27: Book partial at each weekly HVN — do not hold for one target only
- RULE 28: POC closes below from above = exit immediately, no averaging down

**FAILED SETUP RULES**
- RULE 29: Second touch of same level after stop-out = valid but higher bar required
- RULE 30: Two failed touches at the same level = do not attempt a third — wait for VAL

**POST-VAH BREAKOUT RULES**
- RULE 31: Post-VAH breakout retest has NO time expiry — wait as long as price
  stays within 20% of VAH above the breakout level
- RULE 32: If price extends more than 20% above VAH without retesting, original
  VAH retest setup is void — plot Fixed Range VP on post-breakout range,
  identify new weekly POC, apply Strategy 1A at new POC.
  Old VAH remains valid as secondary support only
- RULE 33: If a new weekly HVN forms above VAH during the run before retest occurs,
  that HVN becomes the retest candidate — apply Strategy 1A there.
  Old VAH remains valid as deeper support only

**STRATEGY 3 RULES**
- RULE 34: Strategy 3 confluence is defined by two or more weekly VP features
  (POC, HVN, VAL, VAH) clustering within 25 points — weekly chart only.
  Daily and 4H levels never count as confluence under any strategy

**OBSTACLE RULES**
- RULE 35: Daily and 4H HVNs are obstacles only — price may stall or react at them
  but they are never entry triggers and never price targets.
  All targets come exclusively from weekly chart levels

## 32. Candle Meaning Quick Reference

| Candle at level                  | At resistance     | At support          |
|-----------------------------------|---------------------|------------------------|
| Green + high vol + close above    | Breakout ✅          | Bounce ✅              |
| Red + high vol + close below      | Rejection ❌         | Breakdown ❌           |
| Red + high vol + close above      | Rejection ❌         | Weak bounce ⚠️         |
| Green + low volume                | Weak breakout ⚠️     | Weak bounce ⚠️         |
| Doji                               | Indecision — wait    | Indecision — wait      |

---

*Master framework — consolidated from Volume Profile Trading System and Candle Rules Refined.*
*Updated June 2026 — Strategy 3 redefined (weekly-only confluence), Strategy 2 retest expiry corrected (no time limit, 20% extension rule added), Rules 31–35 added.*
