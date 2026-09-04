"""
Portfolio & State Management Module
Tracks cash, active holdings, order lifecycle, trailing stops, and execution discrepancies.
"""
import os, json, uuid
from typing import Optional
from datetime import datetime
import pytz

from config import (
    PORTFOLIO_FILE, INITIAL_CAPITAL, PER_TRADE_AMOUNT,
    MAX_CONCURRENT_POSITIONS, HARD_STOP_LOSS_PCT, TOTAL_COST,
    CURRENCY
)

IST = pytz.timezone("Asia/Kolkata")


def default_portfolio() -> dict:
    return {
        "capital": float(INITIAL_CAPITAL),
        "invested": 0.0,
        "positions": {},       # ticker -> position dict
        "closed_trades": [],   # list of closed trade dicts
        "orders": [],          # audit list of all orders
        "discrepancies": [],   # audit list of flagged anomalies/mistakes
        "last_updated": datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")
    }


def load_portfolio() -> dict:
    os.makedirs(os.path.dirname(PORTFOLIO_FILE), exist_ok=True)
    if not os.path.exists(PORTFOLIO_FILE):
        p = default_portfolio()
        save_portfolio(p)
        return p
    try:
        with open(PORTFOLIO_FILE, "r", encoding="utf-8") as f:
            p = json.load(f)
            p.setdefault("capital", float(INITIAL_CAPITAL))
            p.setdefault("invested", 0.0)
            p.setdefault("positions", {})
            p.setdefault("closed_trades", [])
            p.setdefault("orders", [])
            p.setdefault("discrepancies", [])
            return p
    except Exception as e:
        print(f"[PORTFOLIO] Load error: {e}, recreating default")
        p = default_portfolio()
        save_portfolio(p)
        return p


def save_portfolio(p: dict):
    p["last_updated"] = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")
    os.makedirs(os.path.dirname(PORTFOLIO_FILE), exist_ok=True)
    tmp = PORTFOLIO_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(p, f, indent=2, default=str)
    os.replace(tmp, PORTFOLIO_FILE)


def can_take_trade(p: dict) -> bool:
    """Check if capital and slot limits allow taking another trade."""
    open_count = len(p.get("positions", {}))
    has_slot = open_count < MAX_CONCURRENT_POSITIONS
    has_cash = p.get("capital", 0.0) >= PER_TRADE_AMOUNT
    return has_slot and has_cash


def add_order(p: dict, ticker: str, side: str, order_type: str,
              expected_price: float, fill_price: float, qty: int,
              status: str = "FILLED", note: str = "") -> dict:
    """Log order to audit trail with slippage tracking."""
    slippage = round(fill_price - expected_price, 2) if (fill_price and expected_price) else 0.0
    order = {
        "Timestamp": datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S"),
        "Order_ID": uuid.uuid4().hex[:10].upper(),
        "Ticker": ticker,
        "Side": side,
        "Type": order_type,
        "Expected_Price": round(expected_price, 2) if expected_price else None,
        "Fill_Price": round(fill_price, 2) if fill_price else None,
        "Slippage": slippage,
        "Qty": qty,
        "Status": status,
        "Note": note
    }
    p.setdefault("orders", []).append(order)
    return order


def add_discrepancy(p: dict, category: str, ticker: str, details: str, severity: str = "WARNING"):
    """Record an anomaly or potential bot mistake for review."""
    item = {
        "Timestamp": datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S"),
        "Category": category,      # SLIPPAGE_ALERT, MISSED_EXIT, GAP_DOWN, DATA_ANOMALY
        "Ticker": ticker,
        "Severity": severity,      # INFO, WARNING, CRITICAL
        "Details": details
    }
    p.setdefault("discrepancies", []).append(item)
    print(f"[AUDIT DISCREPANCY] [{severity}] {ticker}: {details}")


def open_position(p: dict, ticker: str, entry_price: float, entry_date: str) -> Optional[dict]:
    """Open new position with strict sizing and risk control."""
    if not can_take_trade(p):
        return None

    qty = max(int(PER_TRADE_AMOUNT / entry_price), 1)
    actual_invested = round(qty * entry_price, 2)
    sl_price = round(entry_price * (1.0 - HARD_STOP_LOSS_PCT / 100.0), 2)

    p["capital"] -= actual_invested
    p["invested"] += actual_invested

    pos = {
        "ticker": ticker,
        "entry_date": entry_date,
        "entry_price": round(entry_price, 2),
        "qty": qty,
        "invested": actual_invested,
        "current_sl": sl_price,
        "highest_high": round(entry_price, 2),
        "days_held": 0,
        "last_price": round(entry_price, 2),
        "unrealized_pnl_pct": 0.0
    }
    p["positions"][ticker] = pos

    add_order(p, ticker, "BUY", "MARKET", expected_price=entry_price,
              fill_price=entry_price, qty=qty, status="FILLED", note="Entry Breakout")
    return pos


def close_position(p: dict, ticker: str, exit_price: float, exit_date: str,
                   exit_reason: str) -> Optional[dict]:
    """Close active position and record realized P&L."""
    pos = p.get("positions", {}).pop(ticker, None)
    if not pos:
        return None

    entry_price = pos["entry_price"]
    qty = pos["qty"]
    invested = pos["invested"]

    gross_pct = round((exit_price - entry_price) / entry_price * 100.0, 3)
    net_pct = round(gross_pct - (TOTAL_COST * 100.0), 3)
    pnl_amount = round(invested * (net_pct / 100.0), 2)
    returned_cash = round(invested + pnl_amount, 2)

    p["capital"] += returned_cash
    p["invested"] = max(0.0, round(p["invested"] - invested, 2))

    trade = {
        "ticker": ticker,
        "entry_date": pos["entry_date"],
        "exit_date": exit_date,
        "entry_price": entry_price,
        "exit_price": round(exit_price, 2),
        "qty": qty,
        "invested": invested,
        "pnl_amount": pnl_amount,
        "gross_pnl_pct": gross_pct,
        "net_pnl_pct": net_pct,
        "exit_reason": exit_reason,
        "days_held": pos.get("days_held", 0)
    }
    p.setdefault("closed_trades", []).append(trade)

    add_order(p, ticker, "SELL", "MARKET", expected_price=exit_price,
              fill_price=exit_price, qty=qty, status="FILLED", note=exit_reason)

    # Check for discrepancy: if loss exceeded planned hard SL by > 1% (e.g. gap down)
    if net_pct < -(HARD_STOP_LOSS_PCT + 1.0):
        add_discrepancy(p, "GAP_DOWN_SLIPPAGE", ticker,
                        f"Loss of {net_pct:.2f}% exceeded target SL of -{HARD_STOP_LOSS_PCT}%. Exit: {exit_price}")

    return trade
