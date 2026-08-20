"""
Portfolio Management - Paper trade tracking
"""
import json, os
from datetime import datetime
from typing import Dict, List, Optional
import pandas as pd
import numpy as np
from config import PORTFOLIO_FILE, INITIAL_CAPITAL, TOTAL_COST

def load_portfolio() -> Dict:
    if os.path.exists(PORTFOLIO_FILE):
        with open(PORTFOLIO_FILE) as f:
            return json.load(f)
    return {
        "capital": INITIAL_CAPITAL,
        "initial_capital": INITIAL_CAPITAL,
        "open_positions": [],
        "closed_trades": [],
        "total_pnl": 0.0,
        "wins": 0,
        "losses": 0,
    }

def save_portfolio(portfolio: Dict):
    os.makedirs(os.path.dirname(PORTFOLIO_FILE), exist_ok=True)
    with open(PORTFOLIO_FILE, "w") as f:
        json.dump(portfolio, f, indent=2, default=str)

def enter_position(portfolio: Dict, ticker: str, entry_price: float,
                   sl: float, t1: float, t2: float, details: Dict) -> Dict:
    """Enter a new paper position."""
    capital = portfolio["capital"]
    risk_amount = capital * 0.02  # 2% risk
    qty = int(risk_amount / (entry_price * 0.025))  # risk per share
    if qty < 1:
        qty = 1

    pos = {
        "ticker": ticker,
        "entry_date": datetime.now().strftime("%Y-%m-%d"),
        "entry_price": round(entry_price, 2),
        "qty": qty,
        "sl": round(sl, 2),
        "t1": round(t1, 2),
        "t2": round(t2, 2),
        "status": "OPEN",
        "sl_moved_to_be": False,
        "half_booked": False,
        "pnl": 0.0,
        "exit_price": None,
        "exit_date": None,
        "exit_reason": None,
        "details": details,
    }
    portfolio["open_positions"].append(pos)
    save_portfolio(portfolio)
    return pos

def check_exits(portfolio: Dict, data_15m_by_date: Dict[str, pd.DataFrame]) -> List[Dict]:
    """Check open positions for SL/T1/T2 exits using 15m CLOSE-based logic."""
    closed = []
    remaining = []

    for pos in portfolio["open_positions"]:
        if pos["status"] != "OPEN":
            remaining.append(pos)
            continue

        trade_date = pos["entry_date"]
        if trade_date not in data_15m_by_date:
            remaining.append(pos)
            continue

        df_15m = data_15m_by_date[trade_date]
        if len(df_15m) < 2:
            remaining.append(pos)
            continue

        entry_price = pos["entry_price"]
        sl = pos["sl"]
        t1 = pos["t1"]
        t2 = pos["t2"]
        sl_moved = pos["sl_moved_to_be"]
        half_booked = pos["half_booked"]
        pnl = pos.get("pnl", 0.0)

        result = None
        exit_price = None
        exit_reason = None

        for i in range(len(df_15m)):
            cl = df_15m.iloc[i]["Close"]

            if not sl_moved:
                if cl <= sl:
                    result = "SL"
                    exit_price = sl
                    exit_reason = "stop_loss"
                    pnl = (sl - entry_price) / entry_price * 100.0
                    break
                if cl >= t2:
                    result = "T2"
                    exit_price = t2
                    exit_reason = "target2"
                    pnl = (t2 - entry_price) / entry_price * 100.0
                    break
                if cl >= t1:
                    sl_moved = True
                    half_booked = True
                    sl = entry_price  # move SL to breakeven
                    pnl = 0.5 * (t1 - entry_price) / entry_price * 100.0
                    continue
            else:
                if cl <= sl:
                    result = "T1_BE"
                    exit_price = sl
                    exit_reason = "t1_be_sl"
                    # pnl already has 50% from T1, remaining at BE = 0
                    break
                if cl >= t2:
                    result = "T2"
                    exit_price = t2
                    exit_reason = "target2"
                    pnl += 0.5 * (t2 - entry_price) / entry_price * 100.0
                    break

        if result is None:
            # Time exit at last candle
            last_cl = df_15m.iloc[-1]["Close"]
            result = "TIME"
            exit_price = last_cl
            exit_reason = "time_exit"
            if not half_booked:
                pnl = (last_cl - entry_price) / entry_price * 100.0
            else:
                pnl += 0.5 * (last_cl - entry_price) / entry_price * 100.0

        net_pnl = pnl - TOTAL_COST * 100.0
        pos["status"] = "CLOSED"
        pos["pnl"] = round(net_pnl, 3)
        pos["exit_price"] = round(exit_price, 2)
        pos["exit_date"] = datetime.now().strftime("%Y-%m-%d")
        pos["exit_reason"] = result
        pos["sl_moved_to_be"] = sl_moved
        pos["half_booked"] = half_booked

        portfolio["closed_trades"].append(pos)
        portfolio["total_pnl"] = round(portfolio.get("total_pnl", 0) + net_pnl, 3)
        if net_pnl > 0:
            portfolio["wins"] = portfolio.get("wins", 0) + 1
        else:
            portfolio["losses"] = portfolio.get("losses", 0) + 1
        portfolio["capital"] = round(portfolio.get("capital", INITIAL_CAPITAL) + (entry_price * pos["qty"] * net_pnl / 100.0), 2)
        closed.append(pos)

    portfolio["open_positions"] = remaining
    save_portfolio(portfolio)
    return closed

def get_portfolio_summary(portfolio: Dict) -> str:
    """Generate a text summary of portfolio state."""
    cap = portfolio.get("capital", INITIAL_CAPITAL)
    init = portfolio.get("initial_capital", INITIAL_CAPITAL)
    pnl = portfolio.get("total_pnl", 0.0)
    wins = portfolio.get("wins", 0)
    losses = portfolio.get("losses", 0)
    total = wins + losses
    wr = round(wins / total * 100, 1) if total > 0 else 0
    ret = round((cap - init) / init * 100, 2)
    open_count = len([p for p in portfolio.get("open_positions", []) if p["status"] == "OPEN"])

    lines = [
        f"PORTFOLIO SUMMARY",
        f"Capital: Rs {cap:,.0f} ({ret:+.2f}%)",
        f"Total P&L: Rs {pnl:+,.0f}",
        f"Win/Loss: {wins}W / {losses}L ({wr}% WR)",
        f"Open Positions: {open_count}",
    ]

    if portfolio.get("open_positions"):
        lines.append("\nOPEN POSITIONS:")
        for p in portfolio["open_positions"]:
            if p["status"] == "OPEN":
                lines.append(f"  {p['ticker']} @ Rs {p['entry_price']} | SL {p['sl']} T1 {p['t1']} T2 {p['t2']}")

    if portfolio.get("closed_trades"):
        lines.append("\nRECENT CLOSED:")
        for t in portfolio["closed_trades"][-5:]:
            lines.append(f"  {t['ticker']} {t['exit_reason']} | P&L {t['pnl']:+.2f}%")

    return "\n".join(lines)
