"""
Enhanced Multi-Sheet Excel Auditor & Mistake-Tracker
Generates data/trade_log.xlsx with 7 dedicated tabs for forensic tracking of every decision.
"""
import os
from datetime import datetime
import pandas as pd
import openpyxl
import pytz

from config import EXCEL_FILE, MASTER_EXCEL_FILE, CURRENCY

IST = pytz.timezone("Asia/Kolkata")

SHEET_SCHEMAS = {
    "Scans": [
        "Date", "Time", "Ticker", "Close", "SMA20", "Upper_Band",
        "EMA9", "EMA21", "Vol_Ratio", "Qualifies", "Rejection_Or_Status"
    ],
    "Signals": [
        "Date", "Time", "Ticker", "Signal_Price", "Initial_SL",
        "Vol_Ratio", "Status"
    ],
    "Active_Holdings": [
        "Ticker", "Entry_Date", "Entry_Price", "Qty", "Invested",
        "Current_Price", "Unrealized_PnL_Pct", "Hard_SL", "Highest_High",
        "Days_Held", "Status"
    ],
    "Trades": [
        "Entry_Date", "Exit_Date", "Ticker", "Entry_Price", "Exit_Price",
        "Qty", "Invested", "Gross_PnL_Pct", "Net_PnL_Pct", "PnL_Amount",
        "Exit_Reason", "Days_Held", "Status"
    ],
    "Orders": [
        "Timestamp", "Order_ID", "Ticker", "Side", "Type",
        "Expected_Price", "Fill_Price", "Slippage", "Qty", "Status", "Note"
    ],
    "Discrepancies": [
        "Timestamp", "Category", "Ticker", "Severity", "Details"
    ],
    "Portfolio": [
        "Date", "Time", "Cash_Available", "Invested_Capital", "Total_Equity",
        "Realized_PnL", "Win_Rate_Pct", "Total_Trades", "Open_Positions"
    ]
}


def ensure_excel():
    os.makedirs(os.path.dirname(EXCEL_FILE), exist_ok=True)
    if not os.path.exists(EXCEL_FILE):
        with pd.ExcelWriter(EXCEL_FILE, engine="openpyxl") as writer:
            for sname, cols in SHEET_SCHEMAS.items():
                pd.DataFrame(columns=cols).to_excel(writer, sheet_name=sname, index=False)
        print(f"[EXCEL] Created audit workbook: {EXCEL_FILE}")


def append_to_sheet(sheet_name: str, rows_list: list):
    """Appends records to specified sheet while preserving history."""
    if not rows_list:
        return
    ensure_excel()
    try:
        existing = pd.read_excel(EXCEL_FILE, sheet_name=sheet_name)
        new_df = pd.DataFrame(rows_list)
        if existing.empty:
            combined = new_df
        else:
            combined = pd.concat([existing.dropna(how="all"), new_df.dropna(how="all")], ignore_index=True)
    except Exception:
        combined = pd.DataFrame(rows_list)

    with pd.ExcelWriter(EXCEL_FILE, engine="openpyxl", mode="a", if_sheet_exists="replace") as writer:
        combined.to_excel(writer, sheet_name=sheet_name, index=False)


def overwrite_sheet(sheet_name: str, rows_list: list):
    """Overwrites dynamic state sheets (like Active_Holdings, Orders, Discrepancies)."""
    ensure_excel()
    target_cols = SHEET_SCHEMAS[sheet_name]
    if rows_list:
        # Standardize keys
        standardized_rows = []
        for r in rows_list:
            row_dict = {}
            for col in target_cols:
                # search case-insensitively
                val = next((v for k, v in r.items() if k.lower() == col.lower()), None)
                row_dict[col] = val
            standardized_rows.append(row_dict)
        df = pd.DataFrame(standardized_rows, columns=target_cols)
    else:
        df = pd.DataFrame(columns=target_cols)

    with pd.ExcelWriter(EXCEL_FILE, engine="openpyxl", mode="a", if_sheet_exists="replace") as writer:
        df.to_excel(writer, sheet_name=sheet_name, index=False)


def write_full_audit(scan_results: list, signals: list, portfolio: dict):
    """Writes the full comprehensive audit log after a bot run."""
    ensure_excel()
    now = datetime.now(IST)
    d_str = now.strftime("%Y-%m-%d")
    t_str = now.strftime("%H:%M:%S")

    # 1. Scans Sheet
    scan_rows = []
    for s in scan_results:
        m = s.get("metrics", {})
        scan_rows.append({
            "Date": d_str,
            "Time": t_str,
            "Ticker": s.get("ticker"),
            "Close": m.get("close"),
            "SMA20": m.get("upper_band"),
            "Upper_Band": m.get("upper_band"),
            "EMA9": m.get("ema9"),
            "EMA21": m.get("ema21"),
            "Vol_Ratio": m.get("vol_ratio"),
            "Qualifies": s.get("qualifies"),
            "Rejection_Or_Status": s.get("reason")
        })
    append_to_sheet("Scans", scan_rows)

    # 2. Signals Sheet
    sig_rows = []
    for sig in signals:
        sig_rows.append({
            "Date": d_str,
            "Time": t_str,
            "Ticker": sig.get("ticker"),
            "Signal_Price": sig.get("close"),
            "Initial_SL": sig.get("initial_sl"),
            "Vol_Ratio": sig.get("vol_ratio"),
            "Status": "QUEUED"
        })
    append_to_sheet("Signals", sig_rows)

    # 3. Active Holdings Sheet (Current State Snapshot)
    holdings_rows = []
    for t, pos in portfolio.get("positions", {}).items():
        holdings_rows.append({
            "Ticker": t,
            "Entry_Date": pos.get("entry_date"),
            "Entry_Price": pos.get("entry_price"),
            "Qty": pos.get("qty"),
            "Invested": pos.get("invested"),
            "Current_Price": pos.get("last_price"),
            "Unrealized_PnL_Pct": pos.get("unrealized_pnl_pct"),
            "Hard_SL": pos.get("current_sl"),
            "Highest_High": pos.get("highest_high"),
            "Days_Held": pos.get("days_held"),
            "Status": "OPEN"
        })
    overwrite_sheet("Active_Holdings", holdings_rows)

    # 4. Trades Sheet (Closed Trades History)
    trade_rows = []
    for tr in portfolio.get("closed_trades", []):
        trade_rows.append({
            "Entry_Date": tr.get("entry_date"),
            "Exit_Date": tr.get("exit_date"),
            "Ticker": tr.get("ticker"),
            "Entry_Price": tr.get("entry_price"),
            "Exit_Price": tr.get("exit_price"),
            "Qty": tr.get("qty"),
            "Invested": tr.get("invested"),
            "Gross_PnL_Pct": tr.get("gross_pnl_pct"),
            "Net_PnL_Pct": tr.get("net_pnl_pct"),
            "PnL_Amount": tr.get("pnl_amount"),
            "Exit_Reason": tr.get("exit_reason"),
            "Days_Held": tr.get("days_held"),
            "Status": "CLOSED"
        })
    overwrite_sheet("Trades", trade_rows)

    # 5. Orders Sheet
    overwrite_sheet("Orders", portfolio.get("orders", []))

    # 6. Discrepancies (Mistake Catcher)
    overwrite_sheet("Discrepancies", portfolio.get("discrepancies", []))

    # 7. Portfolio Summary
    cash = portfolio.get("capital", 0.0)
    invested = portfolio.get("invested", 0.0)
    closed = portfolio.get("closed_trades", [])
    total_trades = len(closed)
    wins = sum(1 for tr in closed if tr.get("pnl_amount", 0) > 0)
    win_rate = round(wins / total_trades * 100.0, 1) if total_trades > 0 else 0.0
    realized_pnl = round(sum(tr.get("pnl_amount", 0) for tr in closed), 2)

    portfolio_row = [{
        "Date": d_str,
        "Time": t_str,
        "Cash_Available": round(cash, 2),
        "Invested_Capital": round(invested, 2),
        "Total_Equity": round(cash + invested, 2),
        "Realized_PnL": realized_pnl,
        "Win_Rate_Pct": win_rate,
        "Total_Trades": total_trades,
        "Open_Positions": len(portfolio.get("positions", {}))
    }]
    append_to_sheet("Portfolio", portfolio_row)
    import shutil
    try:
        if os.path.abspath(EXCEL_FILE) != os.path.abspath(MASTER_EXCEL_FILE):
            shutil.copy2(EXCEL_FILE, MASTER_EXCEL_FILE)
    except Exception:
        pass
    print(f"[EXCEL] Audit log successfully updated ({EXCEL_FILE})")

