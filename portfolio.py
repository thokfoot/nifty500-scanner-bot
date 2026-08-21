"""
Portfolio Management - broker-type paper portfolio.

State (data/portfolio.json):
{
  "capital": 200000.0,
  "initial_capital": 200000.0,
  "holdings":      [ OPEN positions (filled, awaiting exit) ],
  "closed_trades": [ completed trade dicts ],
  "orders":        [ order event audit trail ],
  "daily_pnl":     { "YYYY-MM-DD": net_rs },
  "total_pnl": float, "wins": int, "losses": int
}
"""
import json, os
from datetime import datetime
from typing import Dict, List
import pytz
from config import PORTFOLIO_FILE, INITIAL_CAPITAL, PER_TRADE_AMOUNT, MAX_TRADES_PER_DAY

IST = pytz.timezone("Asia/Kolkata")


def ist_today() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d")


def _default() -> Dict:
    return {
        "capital": INITIAL_CAPITAL,
        "initial_capital": INITIAL_CAPITAL,
        "holdings": [],
        "closed_trades": [],
        "orders": [],
        "daily_pnl": {},
        "total_pnl": 0.0,
        "wins": 0,
        "losses": 0,
    }


def load_portfolio() -> Dict:
    if os.path.exists(PORTFOLIO_FILE):
        try:
            with open(PORTFOLIO_FILE) as f:
                data = json.load(f)
        except Exception:
            data = _default()
        # migrate legacy schema
        if "open_positions" in data and "holdings" not in data:
            data["holdings"] = data.pop("open_positions")
        for k, v in _default().items():
            data.setdefault(k, v)
        return data
    return _default()


def save_portfolio(portfolio: Dict):
    os.makedirs(os.path.dirname(PORTFOLIO_FILE), exist_ok=True)
    with open(PORTFOLIO_FILE, "w") as f:
        json.dump(portfolio, f, indent=2, default=str)


def position_size(entry_price: float) -> int:
    """Rs 10,000 fixed per trade."""
    return max(int(PER_TRADE_AMOUNT / entry_price), 1)


def trades_taken(portfolio: Dict, trade_date: str) -> int:
    """Entries filled on a given trade date (closed + still open)."""
    n = sum(1 for t in portfolio.get("closed_trades", [])
            if t.get("entry_date") == trade_date)
    n += sum(1 for h in portfolio.get("holdings", [])
             if h.get("trade_date") == trade_date)
    return n


def can_take_trade(portfolio: Dict, trade_date: str) -> bool:
    return trades_taken(portfolio, trade_date) < MAX_TRADES_PER_DAY


def get_open_positions(portfolio: Dict) -> List[Dict]:
    return [h for h in portfolio.get("holdings", []) if h.get("status") == "OPEN"]


def find_holding(portfolio: Dict, ticker: str, trade_date: str = None):
    for h in portfolio.get("holdings", []):
        if h["ticker"] == ticker and h.get("status") == "OPEN":
            if trade_date is None or h.get("trade_date") == trade_date:
                return h
    return None


def add_position(portfolio: Dict, pos: Dict):
    portfolio.setdefault("holdings", []).append(pos)


def remove_position(portfolio: Dict, pos: Dict):
    portfolio["holdings"] = [
        h for h in portfolio.get("holdings", [])
        if not (h["ticker"] == pos["ticker"]
                and h.get("trade_date") == pos.get("trade_date")
                and h.get("status") == "OPEN")
    ]


def close_position(portfolio: Dict, pos: Dict, trade: Dict) -> Dict:
    """Move an open holding to closed_trades with P&L accounting."""
    remove_position(portfolio, pos)
    record_closed_trade(portfolio, trade)
    return trade


def record_closed_trade(portfolio: Dict, trade: Dict) -> Dict:
    """Record a completed trade into the books (idempotent per call site)."""
    net_pnl = float(trade.get("net_pnl_pct", 0.0))
    investment = float(trade.get("entry_price", 0)) * int(trade.get("qty", 0))
    pnl_amount = round(investment * net_pnl / 100.0, 2)

    portfolio.setdefault("closed_trades", []).append(trade)
    portfolio["total_pnl"] = round(portfolio.get("total_pnl", 0.0) + net_pnl, 3)
    if net_pnl > 0:
        portfolio["wins"] = portfolio.get("wins", 0) + 1
    else:
        portfolio["losses"] = portfolio.get("losses", 0) + 1
    portfolio["capital"] = round(
        portfolio.get("capital", INITIAL_CAPITAL) + pnl_amount, 2)

    dp = portfolio.setdefault("daily_pnl", {})
    d = trade.get("exit_date") or ist_today()
    dp[d] = round(dp.get(d, 0.0) + pnl_amount, 2)
    return trade


def add_order(portfolio: Dict, order: Dict):
    orders = portfolio.setdefault("orders", [])
    orders.append(order)
    if len(orders) > 2000:            # cap audit trail size
        del orders[:len(orders) - 2000]


def get_portfolio_summary(portfolio: Dict) -> str:
    cap = portfolio.get("capital", INITIAL_CAPITAL)
    init = portfolio.get("initial_capital", INITIAL_CAPITAL)
    pnl = portfolio.get("total_pnl", 0.0)
    wins = portfolio.get("wins", 0)
    losses = portfolio.get("losses", 0)
    total = wins + losses
    wr = round(wins / total * 100, 1) if total else 0.0
    ret = round((cap - init) / init * 100, 2)

    lines = [
        "PORTFOLIO SUMMARY",
        f"Capital: Rs {cap:,.0f} ({ret:+.2f}%)",
        f"Total P&L: Rs {pnl:+,.0f}",
        f"Win/Loss: {wins}W / {losses}L ({wr}% WR)",
        f"Open Positions: {len(get_open_positions(portfolio))}",
    ]
    holdings = get_open_positions(portfolio)
    if holdings:
        lines.append("")
        lines.append("OPEN POSITIONS:")
        for p in holdings:
            be = " [SL@BE]" if p.get("half_booked") else ""
            lines.append(f"  {p['ticker']} @ Rs {p['entry_price']} x{p['qty']}"
                         f" | SL {p['sl_eff']} T1 {p['t1']} T2 {p['t2']}{be}")
    closed = portfolio.get("closed_trades", [])
    if closed:
        lines.append("")
        lines.append("RECENT CLOSED:")
        for t in closed[-5:]:
            lines.append(f"  {t['ticker']} {t['exit_reason']} | {t.get('net_pnl_pct', 0):+.2f}% net")
    return "\n".join(lines)
