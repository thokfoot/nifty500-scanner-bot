"""
Pending Signals Store - persists watchlist signals between runs.

Structure of pending_signals.json:
{
  "pending":  [ {ticker, signal_date, prev_close, entry_price, sl, t1, t2, details} ],
  "executed": [ {ticker, signal_date, trade_date, exit_reason, net_pnl_pct} ]
}
"""
import json, os
from datetime import datetime, timedelta
import pytz
from config import PENDING_FILE, SIGNAL_EXPIRY_DAYS

IST = pytz.timezone("Asia/Kolkata")


def _empty() -> dict:
    return {"pending": [], "executed": []}


def load_store() -> dict:
    if os.path.exists(PENDING_FILE):
        try:
            with open(PENDING_FILE) as f:
                data = json.load(f)
            data.setdefault("pending", [])
            data.setdefault("executed", [])
            return data
        except Exception:
            pass
    return _empty()


def save_store(store: dict):
    os.makedirs(os.path.dirname(PENDING_FILE), exist_ok=True)
    with open(PENDING_FILE, "w") as f:
        json.dump(store, f, indent=2, default=str)


def is_duplicate(store: dict, ticker: str, signal_date: str) -> bool:
    """Same ticker+signal_date already pending or already executed."""
    for s in store["pending"]:
        if s["ticker"] == ticker and s["signal_date"] == signal_date:
            return True
    for e in store["executed"]:
        if e["ticker"] == ticker and e["signal_date"] == signal_date:
            return True
    return False


def add_signals(store: dict, signals: list) -> list:
    """Append new unique signals. Returns list of actually-added ones."""
    added = []
    for sig in signals:
        if not is_duplicate(store, sig["ticker"], sig["signal_date"]):
            store["pending"].append(sig)
            added.append(sig)
    return added


def mark_executed(store: dict, sig: dict, trade_date: str, result: dict):
    """Move a signal from pending to executed history."""
    store["pending"] = [s for s in store["pending"]
                        if not (s["ticker"] == sig["ticker"] and s["signal_date"] == sig["signal_date"])]
    store["executed"].append({
        "ticker": sig["ticker"],
        "signal_date": sig["signal_date"],
        "trade_date": trade_date,
        "exit_reason": result.get("exit_reason"),
        "net_pnl_pct": result.get("net_pnl_pct"),
        "executed_at": datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S"),
    })


def cleanup(store: dict) -> list:
    """Drop stale pendings (older than SIGNAL_EXPIRY_DAYS). Returns expired ones."""
    cutoff = (datetime.now(IST) - timedelta(days=SIGNAL_EXPIRY_DAYS)).strftime("%Y-%m-%d")
    keep, expired = [], []
    for s in store["pending"]:
        if s["signal_date"] < cutoff:
            expired.append(s)
        else:
            keep.append(s)
    store["pending"] = keep

    # Trim executed history to last 500 entries
    if len(store["executed"]) > 500:
        store["executed"] = store["executed"][-500:]
    return expired


def find_trade_date(ticker_15m_dates, signal_date: str):
    """Earliest trading date after signal_date that has 15m data. None if not yet."""
    future = sorted(d for d in ticker_15m_dates if d > signal_date)
    return future[0] if future else None
