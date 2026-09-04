# Volatility Expansion & 9/21 EMA Trend-Ride Bot

Autonomous algorithmic trading bot designed for **High-Alpha Volatility Breakouts & Trend-Following** with a **7-Sheet Forensic Mistake-Tracking Logging System**.

Supports both **US Tech Leaders** ($ accounts) and **Indian Momentum Equities** (₹ accounts).

---

## Strategy Architecture

1. **Trigger:** Daily Close breaks above the 20-period Bollinger Upper Band (`Close > SMA20 + 2*STD`).
2. **Trend Filter:** Fast EMA strictly above Slow EMA (`EMA 9 > EMA 21`).
3. **Volume Confirmation:** Volume $> 1.3\times$ the 20-day Volume Moving Average.
4. **Exit Mechanism (Asymmetrical Payoff):**
   - **Hard Disaster Stop Loss:** Strict `-4.5%` stop from entry.
   - **Trend Exhaustion Exit:** Holds winners dynamically until `EMA 9 < EMA 21` (bearish moving average crossover).
   - **No Artificial Profit Cap:** Allows winners to run (+10% to +100%+ multi-week trends).

---

## Forensic Mistake-Tracking Logging (`data/trade_log.xlsx`)

Every run generates or updates `data/trade_log.xlsx` with **7 dedicated audit sheets**:

| Sheet | What It Audits | Purpose |
| :--- | :--- | :--- |
| **`Scans`** | All scanned stocks + exact `Rejection_Or_Status` reason | Instantly see why a stock was taken or rejected (`BELOW_BAND`, `EMA_BEARISH`, `LOW_VOLUME`). |
| **`Signals`** | Qualified breakout signals queue | Audit signal date, breakout price, and initial SL. |
| **`Active_Holdings`** | Live running positions | Real-time tracking of unrealized P&L, current SL, highest price reached, and days held. |
| **`Trades`** | Closed trade history | Gross P&L %, Net P&L % (after fees), PnL amount, and exact exit reason (`TRAILING_SL`, `HARD_SL`, `TREND_EXIT`). |
| **`Orders`** | Every placed/filled order with **Slippage** | Captures difference between expected price and fill price. |
| **`Discrepancies`** | Automated Anomaly & Mistake Catcher | Flags execution warnings, gap-down slippage beyond SL, or missing bars. |
| **`Portfolio`** | Equity curve & Capital stats | Tracks cash balance, invested capital, total equity, and win rate. |

---

## Configuration (`config.py`)

```python
# Market Mode: "US" for US Tech Leaders, "INDIA" for Indian Momentum Stocks
MARKET_MODE = "US"

# Strategy Parameters
BOLLINGER_PERIOD = 20
BOLLINGER_STD = 2.0
FAST_EMA = 9
SLOW_EMA = 21
VOLUME_MULT = 1.3
HARD_STOP_LOSS_PCT = 4.5

# Risk & Capital Management
INITIAL_CAPITAL = 10000.0        # $10,000 for US (or Rs 1,00,000 for India)
PER_TRADE_AMOUNT = 2000.0        # $2,000 per trade (or Rs 20,000 for India)
MAX_CONCURRENT_POSITIONS = 5     # Maximum 5 active slots
```

---

## Automated Execution (GitHub Actions)

The bot runs on GitHub Actions via `.github/workflows/daily_scan.yml`:
1. Executes `main.py` on schedule.
2. Updates `portfolio.json` and `data/trade_log.xlsx`.
3. Commits and pushes state changes back to repository.
4. Delivers instant alerts and the 7-sheet audit workbook to your Telegram.
