"""
Execution & Trade Lifecycle Engine
Manages active holdings, daily trailing ratchets, disaster stop losses, and trend exits.
"""
from datetime import datetime
import pandas as pd
import pytz

from portfolio import (
    can_take_trade, open_position, close_position,
    add_discrepancy, add_order
)

IST = pytz.timezone("Asia/Kolkata")


def manage_active_holdings(portfolio: dict, market_data_map: dict, today_str: str) -> list:
    """
    Evaluates every currently open holding against today's bar:
    1. Check Hard Stop Loss: Low <= current_sl
    2. Check Trend Reversal: EMA9 < EMA21
    3. Update Unrealized P&L and Days Held
    Returns: list of closed trade dictionaries
    """
    closed_this_run = []
    active_tickers = list(portfolio.get("positions", {}).keys())

    for ticker in active_tickers:
        pos = portfolio["positions"].get(ticker)
        if not pos:
            continue

        df = market_data_map.get(ticker)
        if df is None or len(df) == 0:
            add_discrepancy(portfolio, "DATA_MISSING", ticker, f"No bar data available for today {today_str}")
            continue

        latest_bar = df.iloc[-1]
        hi = float(latest_bar["High"])
        lo = float(latest_bar["Low"])
        cl = float(latest_bar["Close"])
        ema9 = float(latest_bar["EMA9"]) if "EMA9" in latest_bar else cl
        ema21 = float(latest_bar["EMA21"]) if "EMA21" in latest_bar else cl

        # Update position tracking
        pos["days_held"] = pos.get("days_held", 0) + 1
        pos["highest_high"] = max(pos.get("highest_high", pos["entry_price"]), hi)
        pos["last_price"] = round(cl, 2)
        pos["unrealized_pnl_pct"] = round((cl - pos["entry_price"]) / pos["entry_price"] * 100.0, 2)

        entry = pos["entry_price"]
        sl = pos["current_sl"]

        # ── EXIT CONDITION 1: Hard Disaster Stop Loss ──
        if lo <= sl:
            actual_exit = sl
            # Check for gap-down opening below SL
            op = float(latest_bar["Open"])
            if op < sl:
                actual_exit = op  # Slipped beyond SL due to opening gap
                add_discrepancy(portfolio, "GAP_DOWN_SLIP", ticker,
                                f"Opened at {op:.2f} below SL of {sl:.2f}. Filled at Open.")

            trade = close_position(portfolio, ticker, exit_price=actual_exit,
                                   exit_date=today_str, exit_reason="HARD_STOP_LOSS")
            if trade:
                closed_this_run.append(trade)
            continue

        # ── EXIT CONDITION 2: Trend Exhaustion (EMA 9 crosses below EMA 21) ──
        if ema9 < ema21 and pos["days_held"] >= 1:
            trade = close_position(portfolio, ticker, exit_price=cl,
                                   exit_date=today_str, exit_reason="TREND_EXIT (EMA9 < EMA21)")
            if trade:
                closed_this_run.append(trade)
            continue

    return closed_this_run


def execute_new_signals(portfolio: dict, qualified_signals: list, today_str: str) -> list:
    """
    Executes newly qualified breakout signals if slots and capital allow.
    Returns: list of opened position dictionaries.
    """
    opened_this_run = []

    # Rank signals by relative volume spike (highest institutional demand first)
    ranked_signals = sorted(qualified_signals, key=lambda x: x.get("vol_ratio", 1.0), reverse=True)

    for sig in ranked_signals:
        ticker = sig["ticker"]

        # Already holding?
        if ticker in portfolio.get("positions", {}):
            continue

        if can_take_trade(portfolio):
            entry_price = sig["close"]
            pos = open_position(portfolio, ticker, entry_price=entry_price, entry_date=today_str)
            if pos:
                opened_this_run.append(pos)
        else:
            add_discrepancy(portfolio, "SKIPPED_CAPITAL_EXHAUSTED", ticker,
                            f"Qualified breakout on {ticker} skipped: all slots full or insufficient cash.",
                            severity="INFO")

    return opened_this_run
