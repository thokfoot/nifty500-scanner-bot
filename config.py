"""
Nifty 500 Volatile Down-Close Paper Trading Bot
Config: all settings in one place
"""
import os

# ── Strategy Parameters (v5 best from grid search) ──
RANGE_PCT = 3.5          # Min daily range %
CLOSE_POS_MAX = 0.25     # Max close position (0=bottom, 1=top)
VOL_MULT = 1.5           # Volume spike multiplier vs 20-day MA
ENTRY_OFFSET_PCT = 0.8   # Entry offset from prev close %
SL_PCT = 2.5             # Stop loss %
TARGET1_PCT = 1.2        # Target 1 (book 50%) %
TARGET2_PCT = 2.8        # Target 2 (book rest) %
RSI_MAX = 40             # RSI must be below this
TIME_EXIT = "15:15"      # Exit at this time if no SL/T2

# ── Costs ──
BROKERAGE = 0.001        # 0.10% per side
SLIPPAGE = 0.0005        # 0.05% per side
TOTAL_COST = (BROKERAGE + SLIPPAGE) * 2  # 0.30% round trip

# ── Capital ──
INITIAL_CAPITAL = 200000.0  # Rs 2 lakh paper capital
PER_TRADE_AMOUNT = 10000.0  # Rs 10,000 fixed per trade
MAX_TRADES_PER_DAY = 8      # Sweet spot from backtest (8/day = 100% WR)

# ── Data ──
DAILY_PERIOD = "1y"
INTRA_PERIOD = "60d"
MAX_WORKERS = 8

# ── Telegram ──
TG_TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("CHAT_ID")

# ── File Paths ──
PORTFOLIO_FILE = "data/portfolio.json"
LOG_DIR = "logs"
