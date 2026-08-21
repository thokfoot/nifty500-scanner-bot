"""
BrokerSimulator - Zerodha/Groww-type paper broker.

Order types simulated:
  ENTRY : LIMIT buy @ entry_price, validity DAY, product MIS
          fills when candle Low <= entry_price
          skipped (NO_FILL) if day Open > entry * 1.015 (gap filter)
  SL    : SL-M @ trigger, placed once entry fills (working)
  T1    : LIMIT @ t1 for 50% qty (working after fill)
          on T1 fill -> cancel SL-M, place SL-M @ breakeven (entry)
  T2    : LIMIT @ t2 for remaining 50% (working after fill)
  TIME  : MARKET @ 15:15 IST (or day end) cancels all working orders

Order lifecycle: PLACED -> FILLED | CANCELLED | REJECTED

Exit rules (whipsaw-safe, CLOSE-based on 15m candles):
  Close <= sl_eff        -> STOP_LOSS @ sl_eff
  Close >= t2            -> T2_HIT @ t2
  Close >= t1            -> T1_HIT (book 50%, SL -> BE, keep monitoring)
  Close <= BE after T1   -> BE_SL @ entry (remaining half)
  last candle & finalize -> TIME_EXIT @ last close

Costs: TOTAL_COST (0.30%) round trip deducted from gross P&L.

Two entry points:
  execute_signal(sig, day_df, finalize)
      -> {"status": "EXECUTED",     "trade": {...}}
         {"status": "OPEN_POSITION","position": {...}}   (filled, still running)
         {"status": "NO_FILL"|"BAD_DATA", "info": str}
  check_position(pos, day_df, finalize)
      -> {"closed": trade|None, "t1_hit": bool, "pos": pos}
"""
import uuid
from datetime import datetime
import pandas as pd
import pytz

from config import TOTAL_COST
from portfolio import position_size

IST = pytz.timezone("Asia/Kolkata")


def _now_str():
    return datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")


class BrokerSimulator:
    """Stateless-per-call broker; order events are returned for logging."""

    def __init__(self, order_sink=None):
        # order_sink: callable(order_dict) called for every order event
        self.order_sink = order_sink or (lambda o: None)

    # ── order helpers ──────────────────────────────────────────────
    def _order(self, ticker, side, otype, price=None, trigger=None,
               qty=0, status="PLACED", note="", oid=None):
        o = {
            "order_id": oid or uuid.uuid4().hex[:10].upper(),
            "ts": _now_str(),
            "ticker": ticker,
            "side": side,
            "type": otype,              # LIMIT | SL-M | MARKET
            "price": round(price, 2) if price else None,
            "trigger": round(trigger, 2) if trigger else None,
            "qty": qty,
            "status": status,           # PLACED | FILLED | CANCELLED | REJECTED
            "note": note,
        }
        self.order_sink(o)
        return o

    # ── public API ─────────────────────────────────────────────────
    def execute_signal(self, sig: dict, day_df: pd.DataFrame, finalize: bool = False) -> dict:
        """Try D+1 entry for a pending signal on its trade-day candles."""
        ticker = sig["ticker"]
        entry = float(sig["entry_price"])
        sl = float(sig["sl"])
        t1 = float(sig["t1"])
        t2 = float(sig["t2"])

        if day_df is None or len(day_df) < 3:
            return {"status": "BAD_DATA", "info": "insufficient 15m data"}

        day_open = float(day_df.iloc[0]["Open"])

        # Gap filter: opened too far above our limit -> never reachable
        if day_open > entry * 1.015:
            self._order(ticker, "BUY", "LIMIT", price=entry, qty=position_size(entry),
                        status="REJECTED", note=f"gap-up open {day_open:.2f} > entry*1.015")
            return {"status": "NO_FILL", "info": f"gap_up open {day_open:.2f}"}

        # Place ENTRY LIMIT (DAY, MIS)
        qty = position_size(entry)
        entry_order = self._order(ticker, "BUY", "LIMIT", price=entry, qty=qty,
                                  note="ENTRY MIS DAY")

        # Limit fill: first candle whose Low <= entry
        fill_idx = None
        for i in range(len(day_df)):
            if float(day_df.iloc[i]["Low"]) <= entry:
                fill_idx = i
                break
        if fill_idx is None:
            self._order(ticker, "BUY", "LIMIT", price=entry, qty=qty,
                        status="CANCELLED", note="DAY expired unfilled", oid=entry_order["order_id"])
            return {"status": "NO_FILL", "info": f"day_low {float(day_df['Low'].min()):.2f} > entry {entry:.2f}"}

        entry_ts = str(day_df.index[fill_idx])
        self._order(ticker, "BUY", "LIMIT", price=entry, qty=qty,
                    status="FILLED", note=f"@ {entry_ts}", oid=entry_order["order_id"])
        # Working orders after fill
        self._order(ticker, "SELL", "SL-M", trigger=sl, qty=qty, note="protective SL")
        self._order(ticker, "SELL", "LIMIT", price=t1, qty=max(qty // 2, 1), note="T1 50%")
        self._order(ticker, "SELL", "LIMIT", price=t2, qty=qty - max(qty // 2, 1), note="T2 rest")

        pos = {
            "ticker": ticker,
            "signal_date": sig.get("signal_date", ""),
            "trade_date": str(day_df.index[0])[:10],
            "entry_price": round(entry, 2),
            "qty": qty,
            "sl": round(sl, 2),
            "sl_eff": round(sl, 2),
            "t1": round(t1, 2),
            "t2": round(t2, 2),
            "half_booked": False,
            "pnl_banked_pct": 0.0,
            "entry_ts": entry_ts,
            "last_candle_ts": entry_ts,
            "day_low": float(day_df.iloc[fill_idx:]["Low"].min()),
            "day_high": float(day_df.iloc[fill_idx:]["High"].max()),
            "status": "OPEN",
            "details": sig.get("details", {}),
        }

        outcome = self._walk(pos, day_df, start_idx=fill_idx, finalize=finalize)
        if outcome["closed"] is not None:
            return {"status": "EXECUTED", "trade": outcome["closed"]}
        return {"status": "OPEN_POSITION", "position": pos}

    def check_position(self, pos: dict, day_df: pd.DataFrame, finalize: bool = False) -> dict:
        """Monitor an open position on candles newer than pos['last_candle_ts']."""
        if day_df is None or len(day_df) == 0:
            return {"closed": None, "t1_hit": False, "pos": pos}

        try:
            last_seen = pd.Timestamp(pos["last_candle_ts"])
        except Exception:
            last_seen = None

        if last_seen is not None:
            mask = day_df.index > last_seen
            new_df = day_df[mask]
            start_idx = len(day_df) - len(new_df) if len(new_df) else len(day_df)
        else:
            start_idx = 0

        t1_hit_before = pos.get("half_booked", False)
        outcome = self._walk(pos, day_df, start_idx=start_idx, finalize=finalize)
        t1_event = (not t1_hit_before) and pos.get("half_booked", False)
        return {"closed": outcome["closed"], "t1_hit": t1_event, "pos": pos}

    # ── core candle walker ─────────────────────────────────────────
    def _walk(self, pos: dict, day_df: pd.DataFrame, start_idx: int, finalize: bool) -> dict:
        """Advance position state over candles[start_idx:]. Mutates pos."""
        e = float(pos["entry_price"])
        t1 = float(pos["t1"])
        t2 = float(pos["t2"])
        ticker = pos["ticker"]
        qty = int(pos["qty"])
        half_qty = max(qty // 2, 1)

        closed_trade = None
        i = max(start_idx, 0)

        while i < len(day_df):
            row = day_df.iloc[i]
            cl = float(row["Close"])
            lo = float(row["Low"])
            hi = float(row["High"])
            ts = str(day_df.index[i])
            pos["last_candle_ts"] = ts
            pos["day_low"] = min(pos.get("day_low", lo), lo)
            pos["day_high"] = max(pos.get("day_high", hi), hi)

            if not pos.get("half_booked"):
                if cl <= pos["sl_eff"]:                       # SL-M triggered on close
                    self._order(ticker, "SELL", "SL-M", trigger=pos["sl_eff"], qty=qty,
                                status="FILLED", note=f"STOP_LOSS close {cl:.2f} low {lo:.2f}")
                    gross = (pos["sl_eff"] - e) / e * 100.0
                    closed_trade = self._make_trade(pos, pos["sl_eff"], "STOP_LOSS",
                                                    gross, i - start_idx + 1, lo, hi)
                    break
                if cl >= t2:
                    self._order(ticker, "SELL", "LIMIT", price=t2, qty=qty,
                                status="FILLED", note="T2 full exit")
                    gross = (t2 - e) / e * 100.0
                    closed_trade = self._make_trade(pos, t2, "T2_HIT",
                                                    gross, i - start_idx + 1, lo, hi)
                    break
                if cl >= t1:                                   # book 50%, SL -> BE
                    self._order(ticker, "SELL", "LIMIT", price=t1, qty=half_qty,
                                status="FILLED", note="T1 50% booked")
                    self._order(ticker, "SELL", "SL-M", trigger=e, qty=qty - half_qty,
                                note="SL shifted to breakeven")
                    pos["half_booked"] = True
                    pos["sl_eff"] = round(e, 2)
                    pos["pnl_banked_pct"] = 0.5 * (t1 - e) / e * 100.0
                    i += 1
                    continue
            else:
                if cl <= pos["sl_eff"]:                        # BE stop on remaining half
                    self._order(ticker, "SELL", "SL-M", trigger=pos["sl_eff"],
                                qty=qty - half_qty, status="FILLED",
                                note=f"BE_SL close {cl:.2f}")
                    gross = pos.get("pnl_banked_pct", 0.0)
                    closed_trade = self._make_trade(pos, pos["sl_eff"], "BE_SL",
                                                    gross, i - start_idx + 1, lo, hi)
                    break
                if cl >= t2:
                    self._order(ticker, "SELL", "LIMIT", price=t2, qty=qty - half_qty,
                                status="FILLED", note="T2 remaining 50%")
                    gross = pos.get("pnl_banked_pct", 0.0) + 0.5 * (t2 - e) / e * 100.0
                    closed_trade = self._make_trade(pos, t2, "T2_HIT",
                                                    gross, i - start_idx + 1, lo, hi)
                    break
            i += 1

        if closed_trade is None and finalize and len(day_df) > 0:
            last_cl = float(day_df.iloc[-1]["Close"])
            reason = "TIME_EXIT"
            if pos.get("half_booked"):
                gross = pos.get("pnl_banked_pct", 0.0) + 0.5 * (last_cl - e) / e * 100.0
            else:
                gross = (last_cl - e) / e * 100.0
            self._order(ticker, "SELL", "MARKET", qty=qty,
                        status="FILLED", note=f"time exit @ {last_cl:.2f}")
            closed_trade = self._make_trade(pos, last_cl, reason,
                                            gross, len(day_df) - start_idx,
                                            pos.get("day_low"), pos.get("day_high"))

        return {"closed": closed_trade}

    def _make_trade(self, pos, exit_price, exit_reason, gross_pct, holding_candles,
                    candle_low, candle_high) -> dict:
        gross = round(gross_pct, 3)
        net = round(gross - TOTAL_COST * 100.0, 3)
        return {
            "ticker": pos["ticker"],
            "signal_date": pos.get("signal_date", ""),
            "entry_date": pos.get("trade_date", ""),
            "exit_date": pos.get("trade_date", ""),
            "entry_price": round(float(pos["entry_price"]), 2),
            "exit_price": round(float(exit_price), 2),
            "exit_reason": exit_reason,
            "qty": int(pos["qty"]),
            "sl": pos.get("sl"),
            "sl_eff": pos.get("sl_eff"),
            "t1": pos.get("t1"),
            "t2": pos.get("t2"),
            "half_booked": bool(pos.get("half_booked")),
            "gross_pnl_pct": gross,
            "net_pnl_pct": net,
            "pnl": net,
            "pnl_net": net,
            "pnl_pct": net,
            "holding_candles": max(int(holding_candles), 1),
            "exit_candle_low": round(float(candle_low), 2) if candle_low else None,
            "exit_candle_high": round(float(candle_high), 2) if candle_high else None,
            "details": pos.get("details", {}),
        }


def execute_signal(sig: dict, day_df: pd.DataFrame, finalize: bool = False) -> dict:
    """Module-level convenience wrapper."""
    return BrokerSimulator().execute_signal(sig, day_df, finalize)
