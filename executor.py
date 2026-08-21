"""
Signal Executor - simulates D+1 trade for a pending signal.

Flow for signal from day D (signal_date), executed on day T (trade_date):
  1. Gap filter: if T opens > GAP_MAX_PCT above prev_close -> skip
  2. Limit fill: entry_price = prev_close * (1 - ENTRY_OFFSET_PCT/100)
     filled on first candle whose Low <= entry_price, else no_fill
  3. Exits (CLOSE-based, from fill candle onward):
       close <= SL        -> stop_loss (full exit)
       close >= T2        -> target2   (full exit)
       close >= T1        -> book 50%, SL moves to breakeven
           close <= BE    -> t1_be_sl  (remaining 50% at breakeven)
           close >= T2    -> target2
       end of data        -> time_exit at last candle close
  4. net_pnl_pct = gross - TOTAL_COST*100
"""
import pandas as pd
from config import GAP_MAX_PCT, TOTAL_COST
from portfolio import position_size


def execute_signal(sig: dict, day_df: pd.DataFrame) -> dict:
    """Simulate one pending signal over its trade-day 15m candles.

    Returns dict with keys:
      status: EXECUTED | gap_skip | no_fill | bad_data
      trade: completed trade dict (only when EXECUTED)
    """
    ticker = sig["ticker"]
    entry_price = float(sig["entry_price"])
    sl = float(sig["sl"])
    t1 = float(sig["t1"])
    t2 = float(sig["t2"])
    prev_close = float(sig["prev_close"])

    if day_df is None or len(day_df) < 3:
        return {"status": "bad_data", "trade": None}

    # ── 1. Gap filter ──
    day_open = float(day_df.iloc[0]["Open"])
    gap_pct = (day_open - prev_close) / prev_close * 100.0
    if gap_pct > GAP_MAX_PCT:
        return {"status": "gap_skip", "trade": None,
                "info": f"opened +{gap_pct:.2f}% > {GAP_MAX_PCT}%"}

    # ── 2. Limit fill ──
    fill_idx = None
    for i in range(len(day_df)):
        if float(day_df.iloc[i]["Low"]) <= entry_price:
            fill_idx = i
            break
    if fill_idx is None:
        return {"status": "no_fill", "trade": None}

    # ── 3. CLOSE-based exits from fill candle onward ──
    qty = position_size(entry_price)
    pnl = 0.0
    sl_eff = sl
    half_booked = False
    result = None
    exit_price = None

    for i in range(fill_idx, len(day_df)):
        cl = float(day_df.iloc[i]["Close"])

        if not half_booked:
            if cl <= sl_eff:
                result = "stop_loss"
                exit_price = sl_eff
                pnl = (sl_eff - entry_price) / entry_price * 100.0
                break
            if cl >= t2:
                result = "target2"
                exit_price = t2
                pnl = (t2 - entry_price) / entry_price * 100.0
                break
            if cl >= t1:
                half_booked = True
                sl_eff = entry_price  # breakeven
                pnl += 0.5 * (t1 - entry_price) / entry_price * 100.0
                continue
        else:
            if cl <= sl_eff:
                result = "t1_be_sl"
                exit_price = sl_eff  # breakeven on remaining half
                break
            if cl >= t2:
                result = "target2"
                exit_price = t2
                pnl += 0.5 * (t2 - entry_price) / entry_price * 100.0
                break

    if result is None:
        last_cl = float(day_df.iloc[-1]["Close"])
        result = "time_exit"
        exit_price = last_cl
        if not half_booked:
            pnl = (last_cl - entry_price) / entry_price * 100.0
        else:
            pnl += 0.5 * (last_cl - entry_price) / entry_price * 100.0

    gross_pnl = round(pnl, 3)
    net_pnl = round(gross_pnl - TOTAL_COST * 100.0, 3)

    trade = {
        "ticker": ticker,
        "signal_date": sig["signal_date"],
        "entry_date": str(day_df.index[0])[:10] if hasattr(day_df.index[0], "strftime") else sig.get("trade_date", ""),
        "exit_date": str(day_df.index[-1])[:10] if hasattr(day_df.index[-1], "strftime") else "",
        "entry_price": round(entry_price, 2),
        "qty": qty,
        "sl": round(sl, 2),
        "t1": round(t1, 2),
        "t2": round(t2, 2),
        "exit_price": round(float(exit_price), 2),
        "exit_reason": result,
        "half_booked": half_booked,
        "gross_pnl_pct": gross_pnl,
        "net_pnl_pct": net_pnl,
        "pnl": net_pnl,
        "details": sig.get("details", {}),
    }
    return {"status": "EXECUTED", "trade": trade}
