"""
Nifty 500 Volatile Down-Close Paper Trading Bot
Main orchestrator: scan -> signal -> enter -> monitor -> notify
"""
import sys, os, time, traceback
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import pandas as pd
import numpy as np
import yfinance as yf
import pytz

from config import (
    RANGE_PCT, CLOSE_POS_MAX, VOL_MULT, ENTRY_OFFSET_PCT,
    SL_PCT, TARGET1_PCT, TARGET2_PCT, DAILY_PERIOD, INTRA_PERIOD,
    MAX_WORKERS, INITIAL_CAPITAL
)
from scanner import scan_ticker, is_volatile_down_close
from portfolio import load_portfolio, enter_position, check_exits, get_portfolio_summary, save_portfolio
from notifier import send_msg, send_doc

IST = pytz.timezone("Asia/Kolkata")
LOG_DIR = "logs"

def log(msg):
    ts = datetime.now(IST).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")

def get_nifty500_tickers():
    """Download Nifty 500 list from NSE official CSV."""
    try:
        import io, requests
        url = "https://archives.nseindia.com/content/indices/ind_nifty500list.csv"
        r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        df = pd.read_csv(io.StringIO(r.text))
        tickers = df["Symbol"].tolist()
        log(f"Got {len(tickers)} Nifty 500 tickers from NSE")
        return tickers
    except Exception as e:
        log(f"NSE CSV failed: {e}, using fallback")
        return _fallback_nifty500()

def _fallback_nifty500():
    """Fallback list of major Nifty 500 stocks."""
    return [
        "RELIANCE","TCS","INFY","HDFCBANK","ICICIBANK","SBIN","BHARTIARTL","ITC","LT",
        "KOTAKBANK","BAJFINANCE","HINDUNILVR","ASIANPAINT","AXISBANK","MARUTI","TITAN",
        "SUNPHARMA","ULTRACEMCO","WIPRO","NTPC","HCLTECH","POWERGRID","ONGC","M&M",
        "BAJAJFINSV","ADANIENT","ADANIPORTS","COALINDIA","HDFCLIFE","SBILIFE",
        "TATASTEEL","JSWSTEEL","HINDALCO","GRASIM","CIPLA","DRREDDY","DIVISLAB",
        "EICHERMOT","BAJAJ-AUTO","HEROMOTOCO","BRITANNIA","NESTLEIND","TATACONSUM",
        "APOLLOHOSP","BPCL","UPL","TECHM","INDUSINDBK","TATAMOTORS","TRENT",
        "DMART","ZOMATO","PIDILITIND","BERGEPAINT","CUMMINSIND","ASHOKLEY",
        "MOTHERSON","TVSMOTOR","BAJAJHLDNG","CHOLAFIN","MUTHOOTFIN","PFC","RECLTD",
        "IRCTC","IRFC","HAL","BEL","BDL","MAZDOCK","COCHINSHIP","RVNL","IDEA",
        "TATAPOWER","ADANIGREEN","SUZLON","KPITTECH","PERSISTENT","COFORGE",
        "MPHASIS","LTTS","TATAELXSI","LTIM","POLYCAB","KEI","SBICARD","FEDERALBNK",
        "AUBANK","BANDHANBNK","INDIGO","ABFRL","BANKBARODA","PNB","CANBK",
        "IDFCFIRSTB","VEDL","SAIL","NMDC","OFSS","SONATSOFTW","CROMPTON",
        "VOLTAS","DIXON","LUKE","KAYNES","CDSL","MUTHOOTFIN","MANAPPURAM",
        "IIFL","SUNDARMFIN","TATACOMM","JSL","SOLARINDS","ANGELONE","CAMS",
        "BSE","CAMPUS","MEDANTA","NYKAA","HONASA","DELHIVERY","CLEAN",
        "NAUKRI","INDIAMART","JUSTDIAL","IRB","EXIDEIND","AMARARAJA",
        "ASHOKLEY","MARICO","PGHH","BRITANNIA","NESTLEIND","COLPAL",
        "DABUR","GODREJCP","EMAMILTD","RADICO","UNITDSPR","UBL",
        "DEVYANI","SAPPHIRE","RATNAMANI","TATVA","GRSE","SAIL",
        "HINDCOPPER","NATIONALUM","COCHINSHIP","MAZAGON","BEL","HAL",
    ]

def download_data(tickers, max_workers=8):
    """Download daily + 15m data for all tickers. Returns (daily_dict, 15m_dict)."""
    daily = {}
    data_15m = {}

    def _dl_daily(t):
        try:
            df = yf.download(f"{t}.NS", period=DAILY_PERIOD, interval="1d",
                             auto_adjust=True, progress=False, threads=False)
            if df is not None and not df.empty:
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                df.index = pd.to_datetime(df.index)
                if df.index.tz is not None:
                    df.index = df.index.tz_localize(None)
                if len([c for c in ["Open","High","Low","Close","Volume"] if c in df.columns]) == 5:
                    df = df[["Open","High","Low","Close","Volume"]].dropna()
                    if len(df) >= 30:
                        return t, df
        except:
            pass
        return t, None

    def _dl_15m(t):
        try:
            df = yf.download(f"{t}.NS", period=INTRA_PERIOD, interval="15m",
                             auto_adjust=True, progress=False, threads=False)
            if df is not None and not df.empty:
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                df.index = pd.to_datetime(df.index)
                if df.index.tz is not None:
                    df.index = df.index.tz_localize(None)
                if len([c for c in ["Open","High","Low","Close","Volume"] if c in df.columns]) == 5:
                    df = df[["Open","High","Low","Close","Volume"]].dropna()
                    if len(df) >= 10:
                        by_date = {}
                        for date_val, grp in df.groupby(df.index.date):
                            by_date[str(date_val)] = grp
                        return t, by_date
        except:
            pass
        return t, None

    log(f"Downloading daily data for {len(tickers)} stocks...")
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futs = {pool.submit(_dl_daily, t): t for t in tickers}
        for fut in as_completed(futs):
            t, df = fut.result()
            if df is not None:
                daily[t] = df

    log(f"Daily: {len(daily)}/{len(tickers)} ok")
    log(f"Downloading 15m data...")
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futs = {pool.submit(_dl_15m, t): t for t in daily.keys()}
        for fut in as_completed(futs):
            t, d = fut.result()
            if d is not None:
                data_15m[t] = d

    log(f"15m: {len(data_15m)}/{len(daily)} ok")
    return daily, data_15m

def run_scan(daily, data_15m):
    """Scan all stocks for today's signals.
    Returns: (tradeable_signals, watchlist_signals)
    - tradeable: signal + tomorrow's 15m data exists (can enter now)
    - watchlist: signal detected but no 15m data yet (alert for tomorrow)
    """
    tradeable = []
    watchlist = []

    for ticker, df in daily.items():
        try:
            idx = len(df) - 1
            if idx < 20:
                continue
            ok, details = is_volatile_down_close(df, idx, RANGE_PCT, CLOSE_POS_MAX, VOL_MULT)
            if not ok:
                continue

            sig_date = df.index[idx].strftime("%Y-%m-%d")
            prev_close = df.iloc[idx]["Close"]
            entry_price = prev_close * (1.0 - ENTRY_OFFSET_PCT / 100.0)
            sl = entry_price * (1.0 - SL_PCT / 100.0)
            t1 = entry_price * (1.0 + TARGET1_PCT / 100.0)
            t2 = entry_price * (1.0 + TARGET2_PCT / 100.0)

            entry = {
                "ticker": ticker,
                "signal_date": sig_date,
                "prev_close": round(prev_close, 2),
                "entry_price": round(entry_price, 2),
                "sl": round(sl, 2),
                "t1": round(t1, 2),
                "t2": round(t2, 2),
                "details": details,
            }

            # Check if tomorrow's 15m data exists
            if idx + 1 < len(df):
                trade_date = df.index[idx + 1].strftime("%Y-%m-%d")
                entry["trade_date"] = trade_date
                if trade_date in data_15m.get(ticker, {}):
                    tradeable.append(entry)
                else:
                    watchlist.append(entry)
            else:
                watchlist.append(entry)
        except:
            pass

    log(f"Found {len(tradeable)} tradeable, {len(watchlist)} watchlist")
    return tradeable, watchlist

def format_signals_msg(signals, title="SIGNALS"):
    """Format signals for Telegram."""
    if not signals:
        return ""
    lines = [f"{title} ({len(signals)})"]
    for s in signals:
        d = s["details"]
        lines.append(f"\n{s['ticker']}")
        lines.append(f"  Range: {d['daily_range_pct']}% | CP: {d['close_position']} | RSI: {d['rsi']}")
        lines.append(f"  Entry: Rs {s['entry_price']} | SL: Rs {s['sl']} | T1: Rs {s['t1']} | T2: Rs {s['t2']}")
    return "\n".join(lines)

def format_scan_summary(tickers_ok, tickers_total, signals_count, errors):
    """Format scan summary line."""
    now = datetime.now(IST)
    icon = "✅" if errors == 0 else "⚠️"
    return f"Scan: {tickers_ok}/{tickers_total} stocks {icon} | {signals_count} signals | {errors} errors"

def main():
    now = datetime.now(IST)
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M IST")
    log(f"Starting Nifty 500 Volatile Down-Close Bot - {date_str} {time_str}")

    # ── 1. Load portfolio ──
    portfolio = load_portfolio()
    log(f"Portfolio: Rs {portfolio.get('capital', INITIAL_CAPITAL):,.0f}")

    # ── 2. Get tickers ──
    tickers = get_nifty500_tickers()

    # ── 3. Download data ──
    daily, data_15m = download_data(tickers, MAX_WORKERS)

    # ── 4. Check exits for existing positions ──
    closed = []
    if portfolio.get("open_positions"):
        log(f"Checking exits for {len([p for p in portfolio['open_positions'] if p['status']=='OPEN'])} positions...")
        closed = check_exits(portfolio, data_15m)
        if closed:
            for t in closed:
                log(f"  CLOSED {t['ticker']}: {t['exit_reason']} P&L {t['pnl']:+.2f}%")

    # ── 5. Scan for new signals ──
    tradeable, watchlist = run_scan(daily, data_15m)

    # ── 6. Enter new positions (only from tradeable) ──
    entered = []
    for sig in tradeable:
        # Check if already have position in this ticker
        already_in = any(p["ticker"] == sig["ticker"] and p["status"] == "OPEN"
                        for p in portfolio.get("open_positions", []))
        if already_in:
            log(f"  Skip {sig['ticker']}: already in position")
            continue

        pos = enter_position(
            portfolio,
            ticker=sig["ticker"],
            entry_price=sig["entry_price"],
            sl=sig["sl"],
            t1=sig["t1"],
            t2=sig["t2"],
            details=sig["details"],
        )
        entered.append(pos)
        log(f"  ENTERED {sig['ticker']} @ Rs {sig['entry_price']} | SL {sig['sl']} T1 {sig['t1']} T2 {sig['t2']}")

    # ── 7. Build Telegram message ──
    lines = []
    lines.append(f"{'='*40}")
    lines.append(f"  NIFTY 500 VOLATILE DOWN-CLOSE BOT")
    lines.append(f"  {date_str} {time_str}")
    lines.append(f"{'='*40}")

    # Scan summary
    lines.append("")
    lines.append(format_scan_summary(len(daily), len(tickers), len(tradeable) + len(watchlist), 0))

    # New entries
    if entered:
        lines.append("")
        lines.append(f"NEW ENTRIES ({len(entered)}):")
        for p in entered:
            lines.append(f"  {p['ticker']} @ Rs {p['entry_price']}")
            lines.append(f"    SL: Rs {p['sl']} | T1: Rs {p['t1']} | T2: Rs {p['t2']}")

    # Closed trades
    if closed:
        lines.append("")
        lines.append(f"CLOSED ({len(closed)}):")
        for t in closed:
            emoji = "✅" if t["pnl"] > 0 else "❌"
            lines.append(f"  {emoji} {t['ticker']} {t['exit_reason']} | P&L {t['pnl']:+.2f}%")

    # Signals (tradeable + watchlist)
    if tradeable:
        lines.append("")
        lines.append(format_signals_msg(tradeable, "TRADEABLE (enter now)"))
    if watchlist:
        lines.append("")
        lines.append(format_signals_msg(watchlist, "WATCHLIST (for tomorrow)"))

    # Portfolio summary
    portfolio = load_portfolio()  # reload after changes
    lines.append("")
    lines.append(get_portfolio_summary(portfolio))

    # ── 8. Send to Telegram ──
    msg = "\n".join(lines)
    if len(msg) > 4000:
        msg = msg[:4000]

    log("Sending to Telegram...")
    send_msg(msg)

    # ── 9. Save log (utf-8 for emoji) ──
    os.makedirs(LOG_DIR, exist_ok=True)
    log_path = os.path.join(LOG_DIR, f"scan_{date_str}.txt")
    with open(log_path, "w", encoding="utf-8") as f:
        f.write(msg)
    log(f"Log saved: {log_path}")

    log("Done!")

if __name__ == "__main__":
    main()
