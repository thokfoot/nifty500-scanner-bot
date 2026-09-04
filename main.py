"""
Production Orchestrator: Volatility Expansion & Trend-Ride Bot
Runs complete scan, position management, order execution, multi-sheet audit logging, and Telegram reporting.
"""
import os, sys, time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd
import yfinance as yf
import pytz

from config import (
    MARKET_MODE, US_UNIVERSE, INDIA_UNIVERSE, DATA_PERIOD,
    MAX_WORKERS, EXCEL_FILE, CURRENCY
)
from scanner import compute_indicators, evaluate_ticker
from portfolio import load_portfolio, save_portfolio
from executor import manage_active_holdings, execute_new_signals
from excel_logger import write_full_audit
from telegram_notifier import (
    send_message, send_document, fmt_entry, fmt_exit, fmt_summary
)

IST = pytz.timezone("Asia/Kolkata")


def fetch_universe_data(tickers: list, suffix: str = "") -> dict:
    """Download clean daily data for all universe tickers in parallel."""
    print(f"[{datetime.now(IST).strftime('%H:%M:%S')}] Downloading daily data for {len(tickers)} tickers ({MARKET_MODE})...")
    data_map = {}

    def _dl(t):
        sym = f"{t}{suffix}"
        try:
            d = yf.download(sym, period=DATA_PERIOD, interval="1d", auto_adjust=True, progress=False)
            if isinstance(d.columns, pd.MultiIndex):
                d.columns = d.columns.get_level_values(0)
            d = d.dropna(subset=["Open", "High", "Low", "Close"])
            if len(d) >= 30:
                return t, compute_indicators(d)
        except Exception:
            pass
        return t, None

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futs = {pool.submit(_dl, t): t for t in tickers}
        for f in as_completed(futs):
            t, d = f.result()
            if d is not None:
                data_map[t] = d

    print(f"[{datetime.now(IST).strftime('%H:%M:%S')}] Successfully downloaded & computed indicators for {len(data_map)}/{len(tickers)} tickers.")
    return data_map


def run_cycle():
    start_time = time.time()
    now = datetime.now(IST)
    today_str = now.strftime("%Y-%m-%d")
    print(f"\n{'='*70}\n[RUN START] Volatility Expansion & Trend-Ride Bot | Market: {MARKET_MODE} | {today_str}\n{'='*70}")

    # 1. Load State
    portfolio = load_portfolio()

    # 2. Select Universe
    if MARKET_MODE == "US":
        universe = US_UNIVERSE
        suffix = ""
    else:
        universe = INDIA_UNIVERSE
        suffix = ".NS"

    # 3. Fetch Data
    market_data = fetch_universe_data(universe, suffix=suffix)

    # 4. Step 1: Manage Active Holdings (Check Stop Losses & Trend Exits)
    closed_trades = manage_active_holdings(portfolio, market_data, today_str)
    for tr in closed_trades:
        send_message(fmt_exit(tr))

    # 5. Step 2: Scan Universe for Breakout Signals with Mistake-Tracking Rejections
    scan_results = []
    qualified_signals = []

    for t in universe:
        df = market_data.get(t)
        qualifies, reason, metrics = evaluate_ticker(df, t)
        scan_results.append({
            "ticker": t,
            "qualifies": qualifies,
            "reason": reason,
            "metrics": metrics
        })
        if qualifies:
            qualified_signals.append(metrics)

    print(f"[{datetime.now(IST).strftime('%H:%M:%S')}] Scanned {len(scan_results)} stocks. Found {len(qualified_signals)} qualified breakouts.")

    # 6. Step 3: Execute New Signals
    opened_positions = execute_new_signals(portfolio, qualified_signals, today_str)
    for pos in opened_positions:
        send_message(fmt_entry(pos))

    # 7. Step 4: Save State & Write Enhanced 7-Sheet Audit Excel Workbook
    save_portfolio(portfolio)
    write_full_audit(scan_results, qualified_signals, portfolio)

    # 8. Step 5: Telegram Summary & Excel Delivery
    cash = portfolio.get("capital", 0.0)
    realized_pnl = sum(tr.get("pnl_amount", 0) for tr in portfolio.get("closed_trades", []))
    summary_text = fmt_summary(
        scanned_count=len(scan_results),
        signals_found=len(qualified_signals),
        open_count=len(portfolio.get("positions", {})),
        closed_count=len(closed_trades),
        cash=cash,
        realized_pnl=realized_pnl
    )
    send_message(summary_text)

    if os.path.exists(EXCEL_FILE):
        send_document(EXCEL_FILE, caption=f"Audit Workbook ({today_str}) - 7 Tabs")

    duration = round(time.time() - start_time, 2)
    print(f"[RUN FINISHED] Completed in {duration}s. Audit log saved to {EXCEL_FILE}.\n{'='*70}")


if __name__ == "__main__":
    run_cycle()
