"""
Production Configuration for Volatility Expansion & Trend-Ride Bot
Supports US Mega-Cap / Tech Leaders and Indian Momentum Stocks.
"""
import os

# ── Market Environment ──
# "US" for US Tech Leaders ($ Account)
# "INDIA" for NSE Momentum Leaders (Rs Account)
MARKET_MODE = os.getenv("MARKET_MODE", "US").upper()

# ── Strategy Parameters: Volatility Expansion & Trend-Ride ──
BOLLINGER_PERIOD = 20          # 20-day SMA basis
BOLLINGER_STD = 2.0            # 2 standard deviations for Upper Band
FAST_EMA = 9                   # 9-day EMA for short-term momentum
SLOW_EMA = 21                  # 21-day EMA for primary trend support
VOLUME_MULT = 1.3              # Min volume spike vs 20-day Volume MA
HARD_STOP_LOSS_PCT = 4.5       # Strict 4.5% disaster stop loss

# ── Universes ──
US_UNIVERSE = [
    "NVDA", "TSLA", "AAPL", "MSFT", "META", "AMZN", "GOOGL",
    "AMD", "NFLX", "PLTR", "AVGO", "COIN", "ARM", "UBER", "SMCI", "QCOM"
]

INDIA_UNIVERSE = [
    "TRENT", "BEL", "HAL", "PERSISTENT", "DIXON", "BHARTIARTL",
    "MCX", "CHOLAFIN", "HDFCBANK", "ICICIBANK", "POLYCAB", "VBL", "RELIANCE"
]

# ── Capital & Portfolio Risk Management ──
if MARKET_MODE == "US":
    INITIAL_CAPITAL = float(os.getenv("INITIAL_CAPITAL", "10000.0"))  # $10,000
    PER_TRADE_AMOUNT = float(os.getenv("PER_TRADE_AMOUNT", "2000.0")) # $2,000 per trade
    MAX_CONCURRENT_POSITIONS = int(os.getenv("MAX_CONCURRENT", "5"))   # 5 slots
    TOTAL_COST = 0.0005                                               # 0.05% round trip
    CURRENCY = "$"
else:
    INITIAL_CAPITAL = float(os.getenv("INITIAL_CAPITAL", "100000.0")) # Rs 1 Lakh
    PER_TRADE_AMOUNT = float(os.getenv("PER_TRADE_AMOUNT", "20000.0"))# Rs 20,000 per trade
    MAX_CONCURRENT_POSITIONS = int(os.getenv("MAX_CONCURRENT", "5"))  # 5 slots
    TOTAL_COST = 0.0030                                               # 0.30% round trip
    CURRENCY = "Rs"

# ── Execution Settings ──
DATA_PERIOD = "1y"
MAX_WORKERS = 8

# ── File Paths & State Tracking ──
DATA_DIR = "data"
LOG_DIR = "logs"
PORTFOLIO_FILE = os.path.join(DATA_DIR, f"portfolio_{MARKET_MODE.lower()}.json")
PENDING_FILE = os.path.join(DATA_DIR, f"pending_signals_{MARKET_MODE.lower()}.json")
EXCEL_FILE = os.path.join(DATA_DIR, f"trade_log_{MARKET_MODE.lower()}.xlsx")
MASTER_EXCEL_FILE = os.path.join(DATA_DIR, "trade_log.xlsx")


# ── Telegram Credentials ──
TG_TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("CHAT_ID")
