"""
Nifty 500 Volatile Down-Close Paper Trading Bot
Main orchestrator: scan -> signal -> enter -> monitor -> log -> notify
"""
import sys, os, time, traceback
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd
import numpy as np
import yfinance as yf
import pytz

from config import (
    RANGE_PCT, CLOSE_POS_MAX, VOL_MULT, ENTRY_OFFSET_PCT,
    SL_PCT, TARGET1_PCT, TARGET2_PCT, DAILY_PERIOD, INTRA_PERIOD,
    MAX_WORKERS, INITIAL_CAPITAL, PER_TRADE_AMOUNT
)
from scanner import is_volatile_down_close
from portfolio import load_portfolio, enter_position, check_exits, get_portfolio_summary, save_portfolio
from notifier import send_msg, send_doc
from excel_logger import log_scan, log_signals, log_trade, log_error, log_portfolio, get_excel_path, get_excel_exists

IST = pytz.timezone("Asia/Kolkata")
LOG_DIR = "logs"

def log(msg):
    ts = datetime.now(IST).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")

def get_nifty500_tickers():
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
        "VOLTAS","DIXON","LUKE","KAYNES","CDSL","MANAPPURAM",
        "IIFL","SUNDARMFIN","TATACOMM","JSL","SOLARINDS","ANGELONE","CAMS",
        "BSE","CAMPUS","MEDANTA","NYKAA","HONASA","DELHIVERY","CLEAN",
        "NAUKRI","INDIAMART","JUSTDIAL","IRB","EXIDEIND","AMARARAJA",
        "MARICO","PGHH","COLPAL","DABUR","GODREJCP","EMAMILTD","RADICO",
        "DEVYANI","SAPPHIRE","RATNAMANI","TATVA","GRSE",
        "HINDCOPPER","NATIONALUM","MAZAGON",
    ]

def download_data(tickers, max_workers=8):
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
    now = datetime.now(IST)
    return f"Scan: {tickers_ok}/{tickers_total} stocks | {signals_count} signals | {errors} errors"

def main():
    start_time = time.time()
    now = datetime.now(IST)
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M IST")
    error_count = 0

    log(f"Starting Nifty 500 Volatile Down-Close Bot - {date_str} {time_str}")

    try:
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
            open_count = len([p for p in portfolio["open_positions"] if p["status"] == "OPEN"])
            log(f"Checking exits for {open_count} positions...")
            closed = check_exits(portfolio, data_15m)
            for t in closed:
                log(f"  CLOSED {t['ticker']}: {t['exit_reason']} P&L {t['pnl']:+.2f}%")
                try:
                    log_trade(t, status="CLOSED")
                except Exception as e:
                    log(f"  [LOG ERROR] trade log failed: {e}")
                    error_count += 1

        # ── 5. Scan for new signals ──
        tradeable, watchlist = run_scan(daily, data_15m)

        # ── 6. Enter new positions ──
        entered = []
        for sig in tradeable:
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
            try:
                log_trade(pos, status="ENTERED")
            except Exception as e:
                log(f"  [LOG ERROR] trade log failed: {e}")
                error_count += 1

        # ── 7. Log everything to Excel ──
        duration = time.time() - start_time
        try:
            log_scan(
                stocks_scanned=len(tickers),
                daily_ok=len(daily),
                intraday_ok=len(data_15m),
                signals_found=len(tradeable) + len(watchlist),
                tradeable=len(tradeable),
                watchlist=len(watchlist),
                entered=len(entered),
                closed=len(closed),
                errors=error_count,
                duration=duration,
            )
        except Exception as e:
            log(f"[LOG ERROR] scan log failed: {e}")
            error_count += 1

        try:
            if tradeable:
                log_signals(tradeable, signal_type="TRADEABLE")
            if watchlist:
                log_signals(watchlist, signal_type="WATCHLIST")
        except Exception as e:
            log(f"[LOG ERROR] signal log failed: {e}")
            error_count += 1

        try:
            portfolio = load_portfolio()
            log_portfolio(portfolio)
        except Exception as e:
            log(f"[LOG ERROR] portfolio log failed: {e}")
            error_count += 1

        # ── 8. Build Telegram message ──
        lines = []
        lines.append(f"{'='*40}")
        lines.append(f"  NIFTY 500 VOLATILE DOWN-CLOSE BOT")
        lines.append(f"  {date_str} {time_str}")
        lines.append(f"{'='*40}")

        lines.append("")
        lines.append(format_scan_summary(len(daily), len(tickers), len(tradeable) + len(watchlist), error_count))

        if entered:
            lines.append("")
            lines.append(f"NEW ENTRIES ({len(entered)}):")
            for p in entered:
                lines.append(f"  {p['ticker']} @ Rs {p['entry_price']}")
                lines.append(f"    SL: Rs {p['sl']} | T1: Rs {p['t1']} | T2: Rs {p['t2']}")

        if closed:
            lines.append("")
            lines.append(f"CLOSED ({len(closed)}):")
            for t in closed:
                emoji = "+" if t["pnl"] > 0 else "-"
                lines.append(f"  {emoji} {t['ticker']} {t['exit_reason']} | P&L {t['pnl']:+.2f}%")

        if tradeable:
            lines.append("")
            lines.append(format_signals_msg(tradeable, "TRADEABLE (enter now)"))
        if watchlist:
            lines.append("")
            lines.append(format_signals_msg(watchlist, "WATCHLIST (for tomorrow)"))

        portfolio = load_portfolio()
        lines.append("")
        lines.append(get_portfolio_summary(portfolio))

        # ── 9. Send to Telegram ──
        msg = "\n".join(lines)
        if len(msg) > 4000:
            msg = msg[:4000]

        log("Sending to Telegram...")
        send_msg(msg)

        # ── 10. Send Excel file ──
        if get_excel_exists():
            log("Sending Excel log...")
            send_doc(get_excel_path(), caption=f"Trade Log - {date_str}")

        # ── 11. Save text log ──
        os.makedirs(LOG_DIR, exist_ok=True)
        log_path = os.path.join(LOG_DIR, f"scan_{date_str}.txt")
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(msg)
        log(f"Log saved: {log_path}")

    except Exception as e:
        tb = traceback.format_exc()
        log(f"FATAL ERROR: {e}")
        log(tb)
        try:
            log_error("MAIN", e, tb)
        except:
            pass
        try:
            send_msg(f"BOT ERROR\n{date_str} {time_str}\n{str(e)[:500]}")
        except:
            pass

    log("Done!")

if __name__ == "__main__":
    main()
