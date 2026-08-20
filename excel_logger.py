"""
Excel Logger - comprehensive logging for scans, signals, trades, errors
Creates/updates a multi-sheet Excel file after each run.
"""
import os, traceback
from datetime import datetime
import pandas as pd
import pytz

IST = pytz.timezone("Asia/Kolkata")
EXCEL_FILE = "data/trade_log.xlsx"

SCAN_COLS = [
    "Date", "Time", "Stocks_Scanned", "Daily_Ok", "Intraday_Ok",
    "Signals_Found", "Tradeable", "Watchlist", "Entered", "Closed", "Errors", "Duration_Sec"
]
SIGNAL_COLS = [
    "Date", "Time", "Ticker", "Signal_Date", "Type",
    "Prev_Close", "Entry_Price", "SL", "T1", "T2",
    "Range_Pct", "Close_Pos", "RSI", "SMA20", "Volume_Spike", "Below_VWAP"
]
TRADE_COLS = [
    "Entry_Date", "Exit_Date", "Ticker", "Entry_Price", "Qty",
    "SL", "T1", "T2", "Exit_Price", "Exit_Reason",
    "Gross_PnL_Pct", "Net_PnL_Pct", "Investment", "PnL_Amount", "Status"
]
ERROR_COLS = ["Date", "Time", "Context", "Error", "Traceback"]
PORTFOLIO_COLS = [
    "Date", "Time", "Capital", "Invested", "Available", "Unrealized_PnL",
    "Realized_PnL", "Total_PnL", "Win_Rate", "Total_Trades", "Open_Positions"
]

ALL_SHEETS = {
    "Scans": SCAN_COLS,
    "Signals": SIGNAL_COLS,
    "Trades": TRADE_COLS,
    "Errors": ERROR_COLS,
    "Portfolio": PORTFOLIO_COLS,
}


def _ensure_excel():
    """Create Excel with all sheets if it doesn't exist."""
    os.makedirs(os.path.dirname(EXCEL_FILE), exist_ok=True)
    if not os.path.exists(EXCEL_FILE):
        with pd.ExcelWriter(EXCEL_FILE, engine="openpyxl") as xf:
            for sheet_name, cols in ALL_SHEETS.items():
                pd.DataFrame(columns=cols).to_excel(xf, sheet_name=sheet_name, index=False)
        print(f"[LOG] Created {EXCEL_FILE}")


def _load_sheet(xf, sheet_name, cols):
    """Load existing sheet or create empty DataFrame."""
    try:
        xf_book = xf.book
        if sheet_name in xf_book.sheetnames:
            return pd.read_excel(EXCEL_FILE, sheet_name=sheet_name)
    except:
        pass
    return pd.DataFrame(columns=cols)


def _append_row(df, row_dict):
    """Append a row to DataFrame."""
    new_row = pd.DataFrame([row_dict])
    return pd.concat([df, new_row], ignore_index=True)


def log_scan(stocks_scanned, daily_ok, intraday_ok, signals_found,
             tradeable, watchlist, entered, closed, errors, duration):
    """Log a scan run."""
    now = datetime.now(IST)
    _ensure_excel()

    row = {
        "Date": now.strftime("%Y-%m-%d"),
        "Time": now.strftime("%H:%M:%S"),
        "Stocks_Scanned": stocks_scanned,
        "Daily_Ok": daily_ok,
        "Intraday_Ok": intraday_ok,
        "Signals_Found": signals_found,
        "Tradeable": tradeable,
        "Watchlist": watchlist,
        "Entered": entered,
        "Closed": closed,
        "Errors": errors,
        "Duration_Sec": round(duration, 1),
    }

    try:
        with pd.ExcelWriter(EXCEL_FILE, engine="openpyxl", mode="a", if_sheet_exists="overlay") as xf:
            df = _load_sheet(xf, "Scans", SCAN_COLS)
            df = _append_row(df, row)
            df.to_excel(xf, sheet_name="Scans", index=False)
        print(f"[LOG] Scan logged")
    except Exception as e:
        print(f"[LOG] Scan log error: {e}")


def log_signals(signals, signal_type="WATCHLIST"):
    """Log signals found."""
    if not signals:
        return
    now = datetime.now(IST)
    _ensure_excel()

    rows = []
    for s in signals:
        d = s.get("details", {})
        rows.append({
            "Date": now.strftime("%Y-%m-%d"),
            "Time": now.strftime("%H:%M:%S"),
            "Ticker": s["ticker"],
            "Signal_Date": s.get("signal_date", ""),
            "Type": signal_type,
            "Prev_Close": s.get("prev_close", ""),
            "Entry_Price": s.get("entry_price", ""),
            "SL": s.get("sl", ""),
            "T1": s.get("t1", ""),
            "T2": s.get("t2", ""),
            "Range_Pct": d.get("daily_range_pct", ""),
            "Close_Pos": d.get("close_position", ""),
            "RSI": d.get("rsi", ""),
            "SMA20": d.get("sma20", ""),
            "Volume_Spike": d.get("volume_spike", ""),
            "Below_VWAP": d.get("below_vwap", ""),
        })

    try:
        with pd.ExcelWriter(EXCEL_FILE, engine="openpyxl", mode="a", if_sheet_exists="overlay") as xf:
            df = _load_sheet(xf, "Signals", SIGNAL_COLS)
            for row in rows:
                df = _append_row(df, row)
            df.to_excel(xf, sheet_name="Signals", index=False)
        print(f"[LOG] {len(rows)} signals logged ({signal_type})")
    except Exception as e:
        print(f"[LOG] Signal log error: {e}")


def log_trade(trade, status="CLOSED"):
    """Log a trade (entry or exit)."""
    now = datetime.now(IST)
    _ensure_excel()

    entry_price = trade.get("entry_price", 0)
    qty = trade.get("qty", 0)
    investment = entry_price * qty
    gross_pnl_pct = trade.get("gross_pnl_pct", trade.get("pnl", 0))
    net_pnl_pct = trade.get("net_pnl_pct", trade.get("pnl", 0))
    pnl_amount = round(investment * net_pnl_pct / 100.0, 2)

    row = {
        "Entry_Date": trade.get("entry_date", now.strftime("%Y-%m-%d")),
        "Exit_Date": trade.get("exit_date", ""),
        "Ticker": trade.get("ticker", ""),
        "Entry_Price": entry_price,
        "Qty": qty,
        "SL": trade.get("sl", ""),
        "T1": trade.get("t1", ""),
        "T2": trade.get("t2", ""),
        "Exit_Price": trade.get("exit_price", ""),
        "Exit_Reason": trade.get("exit_reason", trade.get("result", "")),
        "Gross_PnL_Pct": gross_pnl_pct,
        "Net_PnL_Pct": net_pnl_pct,
        "Investment": round(investment, 2),
        "PnL_Amount": pnl_amount,
        "Status": status,
    }

    try:
        with pd.ExcelWriter(EXCEL_FILE, engine="openpyxl", mode="a", if_sheet_exists="overlay") as xf:
            df = _load_sheet(xf, "Trades", TRADE_COLS)
            df = _append_row(df, row)
            df.to_excel(xf, sheet_name="Trades", index=False)
        print(f"[LOG] Trade logged: {trade.get('ticker','')} {status}")
    except Exception as e:
        print(f"[LOG] Trade log error: {e}")


def log_error(context, error, tb_str=None):
    """Log an error."""
    now = datetime.now(IST)
    _ensure_excel()

    row = {
        "Date": now.strftime("%Y-%m-%d"),
        "Time": now.strftime("%H:%M:%S"),
        "Context": context,
        "Error": str(error)[:500],
        "Traceback": (tb_str or "")[:1000],
    }

    try:
        with pd.ExcelWriter(EXCEL_FILE, engine="openpyxl", mode="a", if_sheet_exists="overlay") as xf:
            df = _load_sheet(xf, "Errors", ERROR_COLS)
            df = _append_row(df, row)
            df.to_excel(xf, sheet_name="Errors", index=False)
        print(f"[LOG] Error logged: {context}")
    except Exception as e:
        print(f"[LOG] Error log itself failed: {e}")


def log_portfolio(portfolio):
    """Log portfolio snapshot."""
    now = datetime.now(IST)
    _ensure_excel()

    capital = portfolio.get("capital", 0)
    open_pos = [p for p in portfolio.get("open_positions", []) if p.get("status") == "OPEN"]
    invested = sum(p.get("entry_price", 0) * p.get("qty", 0) for p in open_pos)
    available = capital - invested
    realized = portfolio.get("total_pnl", 0)
    wins = portfolio.get("wins", 0)
    losses = portfolio.get("losses", 0)
    total = wins + losses
    wr = round(wins / total * 100, 1) if total > 0 else 0

    row = {
        "Date": now.strftime("%Y-%m-%d"),
        "Time": now.strftime("%H:%M:%S"),
        "Capital": round(capital, 2),
        "Invested": round(invested, 2),
        "Available": round(available, 2),
        "Unrealized_PnL": 0,
        "Realized_PnL": round(realized, 2),
        "Total_PnL": round(realized, 2),
        "Win_Rate": wr,
        "Total_Trades": total,
        "Open_Positions": len(open_pos),
    }

    try:
        with pd.ExcelWriter(EXCEL_FILE, engine="openpyxl", mode="a", if_sheet_exists="overlay") as xf:
            df = _load_sheet(xf, "Portfolio", PORTFOLIO_COLS)
            df = _append_row(df, row)
            df.to_excel(xf, sheet_name="Portfolio", index=False)
        print(f"[LOG] Portfolio snapshot logged")
    except Exception as e:
        print(f"[LOG] Portfolio log error: {e}")


def get_excel_path():
    """Return the Excel file path."""
    return EXCEL_FILE


def get_excel_exists():
    """Check if Excel file exists."""
    return os.path.exists(EXCEL_FILE)
