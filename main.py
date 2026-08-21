"""
Nifty 500 Volatile Down-Close Paper Trading Bot
Flow:
  1. Load portfolio + pending signals (persisted in repo)
  2. Download daily + 15m data
  3. Execute due pending signals on their D+1 (limit fill + CLOSE-based exits)
  4. Safety-net exit check for any open positions
  5. Scan latest bars for NEW signals -> save as pending (watchlist for next day)
  6. Save state, log Excel, notify Telegram
State is committed back to the repo by the workflow.
"""
import sys, os, time, traceback
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd
import numpy as np
import yfinance as yf
import pytz

from config import (
    RANGE_PCT, CLOSE_POS_MAX, VOL_MULT,
    MAX_WORKERS, INITIAL_CAPITAL, MAX_TRADES_PER_DAY, EXECUTE_AFTER,
)
from scanner import is_volatile_down_close
from portfolio import load_portfolio, check_exits, record_closed_trade, get_portfolio_summary, ist_today
from executor import execute_signal
from signals_store import load_store, save_store, add_signals, mark_executed, cleanup, find_trade_date
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
            df = yf.download(f"{t}.NS", period="1y", interval="1d",
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
            df = yf.download(f"{t}.NS", period="60d", interval="15m",
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

def build_signal(ticker, df, idx):
    """Build a full signal dict from daily bar at idx."""
    ok, details = is_volatile_down_close(df, idx, RANGE_PCT, CLOSE_POS_MAX, VOL_MULT)
    if not ok:
        return None
    sig_date = df.index[idx].strftime("%Y-%m-%d")
    prev_close = float(df.iloc[idx]["Close"])
    entry_price = prev_close * 0.992          # 0.8% below prev close
    sl = entry_price * 0.975                  # 2.5% below entry
    t1 = entry_price * 1.012                  # 1.2% above entry
    t2 = entry_price * 1.028                  # 2.8% above entry
    return {
        "ticker": ticker,
        "signal_date": sig_date,
        "prev_close": round(prev_close, 2),
        "entry_price": round(entry_price, 2),
        "sl": round(sl, 2),
        "t1": round(t1, 2),
        "t2": round(t2, 2),
        "details": details,
    }

def scan_new_signals(daily):
    """Scan recent daily bars for fresh signals.

    Only bars from completed sessions qualify. Today's live (partial) bar is
    accepted only after 15:15 IST (or EXECUTE_NOW=1) so incomplete candles
    don't create false signals with wrong levels.
    Older bars are re-checked too - dedupe in signals_store prevents repeats.
    """
    now = datetime.now(IST)
    today_str = now.strftime("%Y-%m-%d")
    cutoff = now.replace(hour=int(EXECUTE_AFTER[:2]), minute=int(EXECUTE_AFTER[3:5]),
                         second=0, microsecond=0)
    allow_today_bar = now >= cutoff or os.getenv("EXECUTE_NOW") == "1"

    found = []
    for ticker, df in daily.items():
        try:
            n = len(df)
            if n < 25:
                continue
            for idx in (n - 1, n - 2):        # latest bar + previous (Yahoo lag safety)
                if idx < 20:
                    continue
                bar_date = df.index[idx].strftime("%Y-%m-%d")
                if bar_date > today_str:
                    continue
                if bar_date == today_str and not allow_today_bar:
                    continue                  # incomplete live bar - wait for close
                sig = build_signal(ticker, df, idx)
                if sig:
                    found.append(sig)
                    break                     # one signal per ticker
        except Exception:
            pass
    return found

def process_pending(store, portfolio, data_15m):
    """Execute pending signals whose D+1 data has arrived."""
    now = datetime.now(IST)
    today_str = now.strftime("%Y-%m-%d")
    cutoff = now.replace(hour=int(EXECUTE_AFTER[:2]), minute=int(EXECUTE_AFTER[3:5]), second=0, microsecond=0)
    force = os.getenv("EXECUTE_NOW") == "1"

    executed_trades, skipped, deferred = [], [], []
    still_pending = []
    per_day_count = {}          # enforce MAX_TRADES_PER_DAY per trade date

    for sig in store["pending"]:
        try:
            ticker_dates = data_15m.get(sig["ticker"], {}).keys()
            trade_date = find_trade_date(ticker_dates, sig["signal_date"])

            if trade_date is None:
                still_pending.append(sig)     # D+1 data not available yet
                continue

            # Wait for full-day data before simulating today's trade
            if trade_date == today_str and now < cutoff and not force:
                deferred.append(sig)
                still_pending.append(sig)
                continue

            # Max trades per day cap - leftover stays pending, executes next run
            day_count = per_day_count.get(trade_date, 0)
            if day_count >= MAX_TRADES_PER_DAY:
                still_pending.append(sig)
                deferred.append(sig)
                continue

            day_df = data_15m[sig["ticker"]][trade_date]
            outcome = execute_signal(sig, day_df)

            if outcome["status"] == "EXECUTED":
                trade = outcome["trade"]
                # Mark executed FIRST so a later failure can never re-execute it
                mark_executed(store, sig, trade_date, trade)
                try:
                    record_closed_trade(portfolio, trade)
                    executed_trades.append(trade)
                    per_day_count[trade_date] = day_count + 1
                except Exception as e:
                    log(f"  [ERROR] recording {sig['ticker']}: {e}")
                try:
                    log_trade(trade, status="CLOSED")
                except Exception as e:
                    log(f"[LOG ERROR] trade log failed: {e}")
            else:
                info = outcome.get("info", "")
                skipped.append({"sig": sig, "status": outcome["status"], "info": info})
                mark_executed(store, sig, trade_date,
                              {"exit_reason": outcome["status"], "net_pnl_pct": None})
        except Exception as e:
            log(f"  [ERROR] processing {sig['ticker']}: {e}")
            still_pending.append(sig)

    store["pending"] = still_pending
    return executed_trades, skipped, deferred

def format_signal_line(s):
    d = s.get("details", {})
    return (f"  {s['ticker']}\n"
            f"    Range: {d.get('daily_range_pct','?')}% | CP: {d.get('close_position','?')} | RSI: {d.get('rsi','?')}\n"
            f"    Entry: Rs {s['entry_price']} | SL: Rs {s['sl']} | T1: Rs {s['t1']} | T2: Rs {s['t2']}")

def main():
    start_time = time.time()
    now = datetime.now(IST)
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M IST")
    error_count = 0

    log(f"Starting Nifty 500 Volatile Down-Close Bot - {date_str} {time_str}")

    try:
        # ── 1. Load state ──
        portfolio = load_portfolio()
        store = load_store()
        log(f"Portfolio: Rs {portfolio.get('capital', INITIAL_CAPITAL):,.0f} | "
            f"Pending signals: {len(store['pending'])}")

        # ── 2. Get tickers ──
        tickers = get_nifty500_tickers()

        # ── 3. Download data ──
        daily, data_15m = download_data(tickers, MAX_WORKERS)

        # ── 4. Execute due pending signals ──
        executed, skipped, deferred = process_pending(store, portfolio, data_15m)
        for t in executed:
            log(f"  EXECUTED {t['ticker']} @ Rs {t['entry_price']} -> {t['exit_reason']} "
                f"| Net P&L {t['net_pnl_pct']:+.2f}%")
        for s in skipped:
            log(f"  SKIPPED {s['sig']['ticker']} ({s['status']}) {s['info']}")
        if deferred:
            log(f"  DEFERRED {len(deferred)} signal(s) - waiting for full-day data")

        # ── 5. Safety-net exit check for legacy open positions ──
        closed = []
        open_positions = [p for p in portfolio.get("open_positions", []) if p["status"] == "OPEN"]
        if open_positions:
            log(f"Checking exits for {len(open_positions)} open position(s)...")
            closed = check_exits(portfolio, data_15m)
            for t in closed:
                log(f"  CLOSED {t['ticker']}: {t['exit_reason']} P&L {t['pnl']:+.2f}%")
                try:
                    log_trade(t, status="CLOSED")
                except Exception as e:
                    log(f"  [LOG ERROR] trade log failed: {e}")
                    error_count += 1

        # ── 6. Scan for NEW signals ──
        new_signals = scan_new_signals(daily)
        added = add_signals(store, new_signals)
        if added:
            for s in added:
                log(f"  NEW SIGNAL {s['ticker']} (signal date {s['signal_date']}) -> watchlist")

        # ── 7. Expire stale pendings & persist state ──
        expired = cleanup(store)
        if expired:
            log(f"  EXPIRED {len(expired)} stale signal(s)")
        save_store(store)

        # ── 8. Log everything to Excel ──
        duration = time.time() - start_time
        try:
            log_scan(
                stocks_scanned=len(tickers),
                daily_ok=len(daily),
                intraday_ok=len(data_15m),
                signals_found=len(new_signals),
                tradeable=len(executed),
                watchlist=len(added),
                entered=len(executed),
                closed=len(closed),
                errors=error_count,
                duration=duration,
            )
        except Exception as e:
            log(f"[LOG ERROR] scan log failed: {e}")
            error_count += 1

        try:
            if added:
                log_signals(added, signal_type="WATCHLIST")
            if executed:
                log_signals([{"ticker": t["ticker"], "signal_date": t["signal_date"],
                              "prev_close": "", "entry_price": t["entry_price"], "sl": t["sl"],
                              "t1": t["t1"], "t2": t["t2"], "details": t.get("details", {})}
                             for t in executed], signal_type="EXECUTED")
        except Exception as e:
            log(f"[LOG ERROR] signal log failed: {e}")
            error_count += 1

        try:
            log_portfolio(load_portfolio())
        except Exception as e:
            log(f"[LOG ERROR] portfolio log failed: {e}")
            error_count += 1

        # ── 9. Build Telegram message ──
        lines = []
        lines.append(f"{'='*40}")
        lines.append(f"  NIFTY 500 VOLATILE DOWN-CLOSE BOT")
        lines.append(f"  {date_str} {time_str}")
        lines.append(f"{'='*40}")
        lines.append("")
        lines.append(f"Scan: {len(daily)}/{len(tickers)} stocks | "
                     f"{len(executed)} executed | {len(added)} new signals | {error_count} errors")

        if executed:
            lines.append("")
            lines.append(f"TRADES EXECUTED ({len(executed)}):")
            for t in executed:
                emoji = "+" if t["net_pnl_pct"] > 0 else ""
                lines.append(f"  {emoji}{t['ticker']} @ Rs {t['entry_price']} x{t['qty']}")
                lines.append(f"    Exit: {t['exit_reason']} @ Rs {t['exit_price']} | Net P&L: {t['net_pnl_pct']:+.2f}%")

        if skipped:
            lines.append("")
            lines.append(f"SKIPPED ({len(skipped)}):")
            for s in skipped:
                lines.append(f"  {s['sig']['ticker']} - {s['status']} {s['info']}")

        if added:
            lines.append("")
            lines.append(f"NEW WATCHLIST ({len(added)}) - for next trading day:")
            for s in added:
                lines.append(format_signal_line(s))

        if deferred:
            lines.append("")
            lines.append(f"PENDING ({len(deferred)}) - will execute after {EXECUTE_AFTER} IST:")
            for s in deferred:
                lines.append(f"  {s['ticker']} | Entry Rs {s['entry_price']} | SL Rs {s['sl']} | T1 Rs {s['t1']} | T2 Rs {s['t2']}")

        if closed:
            lines.append("")
            lines.append(f"CLOSED OPEN POSITIONS ({len(closed)}):")
            for t in closed:
                emoji = "+" if t["pnl"] > 0 else "-"
                lines.append(f"  {emoji} {t['ticker']} {t['exit_reason']} | P&L {t['pnl']:+.2f}%")

        lines.append("")
        lines.append(get_portfolio_summary(load_portfolio()))

        # ── 10. Send to Telegram ──
        msg = "\n".join(lines)
        if len(msg) > 4000:
            msg = msg[:4000]

        log("Sending to Telegram...")
        send_msg(msg)

        # ── 11. Send Excel file ──
        if get_excel_exists():
            log("Sending Excel log...")
            send_doc(get_excel_path(), caption=f"Trade Log - {date_str}")

        # ── 12. Save text log ──
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
