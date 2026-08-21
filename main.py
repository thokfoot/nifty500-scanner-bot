"""
Nifty 500 Volatile Down-Close Bot - PRODUCTION broker-type orchestrator.

Flow (every run):
  1. Load store (pending_signals.json) + portfolio
  2. Download daily (1y) + 15m (7d) data with retry
  3. LIVE_MODE: process today's partial 15m without the 15:15 guard
  4. Pending signals -> BrokerSimulator (ENTRY LIMIT -> SL-M/T1/T2 working orders)
     - EXECUTED      : closed trade same run
     - OPEN_POSITION : filled, stays in holdings, monitored every run
     - NO_FILL       : limit never touched / gap-up reject
  5. Monitor open holdings for exits on new candles (SL/T2/BE/TIME)
  6. Scan new signals after 15:15 IST (or EXECUTE_NOW), last 2 daily bars
  7. Cleanup expired, save state, Excel (Scans/Signals/Trades/Orders/Portfolio)
  8. Telegram: individual event messages + 1 summary + Excel document

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
from portfolio import (
    load_portfolio, save_portfolio, can_take_trade, add_position,
    find_holding, close_position, record_closed_trade, add_order,
    get_open_positions, get_portfolio_summary, ist_today, position_size,
)
from executor import BrokerSimulator
from signals_store import load_store, save_store, add_signals, mark_executed, cleanup, find_trade_date
from telegram_notifier import (
    send_message, send_document,
    fmt_new_signal, fmt_order_placed, fmt_filled, fmt_no_fill,
    fmt_t1_hit, fmt_closed_trade, fmt_summary,
)
from excel_logger import (
    log_scan, log_signals, log_trade, log_order, log_error, log_portfolio,
    get_excel_path, get_excel_exists,
)

IST = pytz.timezone("Asia/Kolkata")
LOG_DIR = "logs"


def log(msg):
    ts = datetime.now(IST).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


def _cutoff_today(now):
    return now.replace(hour=int(EXECUTE_AFTER[:2]), minute=int(EXECUTE_AFTER[3:5]),
                       second=0, microsecond=0)


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


def _clean_yf(df, min_len):
    if df is None or df.empty:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.index = pd.to_datetime(df.index)
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    if not all(c in df.columns for c in ["Open", "High", "Low", "Close", "Volume"]):
        return None
    df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
    return df if len(df) >= min_len else None


def download_data(tickers, max_workers=8):
    """Daily (1y) + 15m (7d) with one retry pass for failures."""
    daily, data_15m = {}, {}

    def _dl_daily(t):
        try:
            df = yf.download(f"{t}.NS", period="1y", interval="1d",
                             auto_adjust=True, progress=False, threads=False)
            df = _clean_yf(df, 30)
            return t, df
        except Exception:
            return t, None

    def _dl_15m(t):
        try:
            df = yf.download(f"{t}.NS", period="7d", interval="15m",
                             auto_adjust=True, progress=False, threads=False)
            df = _clean_yf(df, 10)
            if df is None:
                return t, None
            by_date = {}
            for date_val, grp in df.groupby(df.index.date):
                by_date[str(date_val)] = grp
            return t, by_date
        except Exception:
            return t, None

    def run_pool(fn, universe, sink, label):
        pending = list(universe)
        for attempt in (1, 2):                      # one retry pass
            if not pending:
                break
            if attempt == 2:
                log(f"{label}: retrying {len(pending)} failed...")
            done = set()
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                futs = {pool.submit(fn, t): t for t in pending}
                for fut in as_completed(futs):
                    t, res = fut.result()
                    if res is not None:
                        sink[t] = res
                        done.add(t)
            pending = [t for t in pending if t not in done]

    log(f"Downloading daily data for {len(tickers)} stocks...")
    run_pool(_dl_daily, tickers, daily, "Daily")
    log(f"Daily: {len(daily)}/{len(tickers)} ok")

    log("Downloading 15m data...")
    run_pool(_dl_15m, daily.keys(), data_15m, "15m")
    log(f"15m: {len(data_15m)}/{len(daily)} ok")
    return daily, data_15m


def build_signal(ticker, df, idx):
    ok, details = is_volatile_down_close(df, idx, RANGE_PCT, CLOSE_POS_MAX, VOL_MULT)
    if not ok:
        return None
    sig_date = df.index[idx].strftime("%Y-%m-%d")
    prev_close = float(df.iloc[idx]["Close"])
    entry_price = prev_close * 0.992          # 0.8% below prev close
    sl = entry_price * 0.975                  # 2.5% below entry
    t1 = entry_price * 1.012                  # +1.2%
    t2 = entry_price * 1.028                  # +2.8%
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
    """Scan last 2 completed daily bars. Today's live bar only after 15:15 IST."""
    now = datetime.now(IST)
    today_str = now.strftime("%Y-%m-%d")
    cutoff = _cutoff_today(now)
    allow_today_bar = now >= cutoff or os.getenv("EXECUTE_NOW") == "1"

    found = []
    for ticker, df in daily.items():
        try:
            n = len(df)
            if n < 25:
                continue
            for idx in (n - 1, n - 2):
                if idx < 20:
                    continue
                bar_date = df.index[idx].strftime("%Y-%m-%d")
                if bar_date > today_str:
                    continue
                if bar_date == today_str and not allow_today_bar:
                    continue
                sig = build_signal(ticker, df, idx)
                if sig:
                    found.append(sig)
                    break
        except Exception:
            pass
    return found


def process_pending(store, portfolio, data_15m, live_mode):
    """Run pending signals through the BrokerSimulator.

    Returns (closed_trades, new_holdings, no_fills, deferred, tg_events, order_events)
    """
    now = datetime.now(IST)
    today_str = now.strftime("%Y-%m-%d")
    cutoff = _cutoff_today(now)

    order_events = []
    broker = BrokerSimulator(order_sink=lambda o: (add_order(portfolio, o),
                                                   order_events.append(o)))

    closed_trades, new_holdings, no_fills, deferred, tg_events = [], [], [], [], []
    still_pending = []

    for sig in store["pending"]:
        try:
            ticker = sig["ticker"]

            # Already holding? never re-enter.
            if find_holding(portfolio, ticker) is not None:
                mark_executed(store, sig, sig.get("signal_date", ""),
                              {"exit_reason": "OPEN_POSITION", "net_pnl_pct": None})
                continue

            ticker_dates = data_15m.get(ticker, {}).keys()
            trade_date = find_trade_date(ticker_dates, sig["signal_date"])
            if trade_date is None:
                still_pending.append(sig)              # D+1 data not there yet
                continue

            finalize = (trade_date < today_str) or (now >= cutoff)
            if trade_date == today_str and not finalize and not live_mode:
                deferred.append(sig)
                still_pending.append(sig)
                continue

            if not can_take_trade(portfolio, trade_date):
                deferred.append(sig)                   # MAX_TRADES_PER_DAY hit
                still_pending.append(sig)
                continue

            day_df = data_15m[ticker][trade_date]
            outcome = broker.execute_signal(sig, day_df, finalize=finalize)
            status = outcome["status"]

            if status == "EXECUTED":
                trade = outcome["trade"]
                mark_executed(store, sig, trade_date, trade)       # FIRST - no double exec
                record_closed_trade(portfolio, trade)
                closed_trades.append(trade)
                tg_events.append(fmt_closed_trade(trade))
                try:
                    log_trade(trade, status="CLOSED")
                except Exception as e:
                    log(f"[LOG ERROR] trade log failed: {e}")
                log(f"  EXECUTED {ticker} @ {trade['entry_price']} -> "
                    f"{trade['exit_reason']} | net {trade['net_pnl_pct']:+.2f}%")

            elif status == "OPEN_POSITION":
                pos = outcome["position"]
                mark_executed(store, sig, trade_date,
                              {"exit_reason": "OPEN_POSITION", "net_pnl_pct": None})
                add_position(portfolio, pos)
                new_holdings.append(pos)
                qty = position_size(float(sig["entry_price"]))
                tg_events.append(fmt_order_placed(sig, qty, trade_date))
                tg_events.append(fmt_filled(pos))
                log(f"  FILLED {ticker} @ {pos['entry_price']} x{pos['qty']} - position OPEN")

            else:                                       # NO_FILL | BAD_DATA
                info = outcome.get("info", "")
                mark_executed(store, sig, trade_date,
                              {"exit_reason": status.lower(), "net_pnl_pct": None})
                no_fills.append({"sig": sig, "info": info})
                if status == "NO_FILL":
                    try:
                        day_low = float(day_df["Low"].min())
                    except Exception:
                        day_low = 0.0
                    tg_events.append(fmt_no_fill(ticker, sig["entry_price"], day_low, info))
                log(f"  SKIPPED {ticker} ({status}) {info}")

        except Exception as e:
            log(f"  [ERROR] processing {sig['ticker']}: {e}")
            still_pending.append(sig)

    store["pending"] = still_pending
    return closed_trades, new_holdings, no_fills, deferred, tg_events, order_events


def monitor_holdings(portfolio, data_15m, live_mode):
    """Check open holdings for exits on candles newer than last processed."""
    now = datetime.now(IST)
    today_str = now.strftime("%Y-%m-%d")
    cutoff = _cutoff_today(now)

    order_events = []
    broker = BrokerSimulator(order_sink=lambda o: (add_order(portfolio, o),
                                                   order_events.append(o)))
    closed_trades, tg_events = [], []

    for pos in get_open_positions(portfolio):
        try:
            ticker = pos["ticker"]
            day_df = data_15m.get(ticker, {}).get(pos.get("trade_date"))
            if day_df is None:
                continue

            finalize = (pos.get("trade_date") < today_str) or (now >= cutoff)
            if not finalize and not live_mode:
                continue

            res = broker.check_position(pos, day_df, finalize=finalize)

            if res["t1_hit"]:
                tg_events.append(fmt_t1_hit(pos))
                log(f"  T1 HIT {ticker} - 50% booked, SL -> BE")

            if res["closed"] is not None:
                trade = close_position(portfolio, pos, res["closed"])
                closed_trades.append(trade)
                tg_events.append(fmt_closed_trade(trade))
                try:
                    log_trade(trade, status="CLOSED")
                except Exception as e:
                    log(f"[LOG ERROR] trade log failed: {e}")
                log(f"  CLOSED {ticker}: {trade['exit_reason']} @ {trade['exit_price']} "
                    f"| net {trade['net_pnl_pct']:+.2f}%")
        except Exception as e:
            log(f"  [ERROR] monitoring {pos.get('ticker')}: {e}")

    return closed_trades, tg_events, order_events


def main():
    start_time = time.time()
    now = datetime.now(IST)
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M IST")
    error_count = 0

    live_mode = os.getenv("LIVE_MODE") == "1" or os.getenv("EXECUTE_NOW") == "1"
    mode_tag = "LIVE" if live_mode else "SAFE"

    log(f"Starting Nifty 500 Bot [{mode_tag}] - {date_str} {time_str}")

    tg_events = []          # individual event messages
    all_order_events = []
    executed_count = 0

    try:
        # ── 1. Load state ──
        portfolio = load_portfolio()
        store = load_store()
        log(f"Capital Rs {portfolio['capital']:,.0f} | Pending {len(store['pending'])} "
            f"| Holdings {len(get_open_positions(portfolio))}")

        # ── 2. Data ──
        tickers = get_nifty500_tickers()
        daily, data_15m = download_data(tickers, MAX_WORKERS)

        # ── 3+4. Pending signals through broker ──
        closed_a, holdings_new, no_fills, deferred, ev, oe = process_pending(
            store, portfolio, data_15m, live_mode)
        tg_events.extend(ev)
        all_order_events.extend(oe)
        executed_count = len(closed_a) + len(holdings_new)

        # ── 5. Monitor open holdings ──
        closed_b, ev, oe = monitor_holdings(portfolio, data_15m, live_mode)
        tg_events.extend(ev)
        all_order_events.extend(oe)
        closed_total = closed_a + closed_b

        # ── 6. New signals (after close only) ──
        added = []
        if now >= _cutoff_today(now) or os.getenv("EXECUTE_NOW") == "1":
            new_signals = scan_new_signals(daily)
            added = add_signals(store, new_signals)
            for s in added:
                tg_events.append(fmt_new_signal(s))
                log(f"  NEW SIGNAL {s['ticker']} ({s['signal_date']}) -> watchlist")
        else:
            log("Signal scan skipped (market still open)")

        # ── 7. Cleanup + persist ──
        expired = cleanup(store)
        if expired:
            log(f"  EXPIRED {len(expired)} stale signal(s)")
        save_store(store)
        save_portfolio(portfolio)

        # ── 8. Excel logging ──
        duration = time.time() - start_time
        try:
            log_scan(
                stocks_scanned=len(tickers), daily_ok=len(daily),
                intraday_ok=len(data_15m), signals_found=len(added),
                tradeable=executed_count, watchlist=len(added),
                entered=executed_count, closed=len(closed_total),
                errors=error_count, duration=duration,
            )
        except Exception as e:
            log(f"[LOG ERROR] scan log failed: {e}")

        try:
            if added:
                log_signals(added, signal_type="WATCHLIST")
        except Exception as e:
            log(f"[LOG ERROR] signal log failed: {e}")

        for o in all_order_events:
            try:
                log_order(o)
            except Exception:
                pass

        try:
            log_portfolio(portfolio)
        except Exception as e:
            log(f"[LOG ERROR] portfolio log failed: {e}")

        # ── 9. Telegram: events then summary then Excel ──
        log(f"Sending {len(tg_events)} TG event message(s)...")
        for msg in tg_events:
            send_message(msg)

        wins = portfolio.get("wins", 0)
        losses = portfolio.get("losses", 0)
        wr = round(wins / (wins + losses) * 100, 1) if (wins + losses) else 0.0
        pnl_today = portfolio.get("daily_pnl", {}).get(date_str, 0.0)
        summary = fmt_summary(
            date_str=f"{date_str} {time_str} [{mode_tag}]",
            scanned=len(daily),
            signals_found=len(added),
            executed=executed_count,
            closed=len(closed_total),
            pnl_rs=pnl_today,
            win_rate=wr,
            pending=[s["ticker"] for s in store["pending"]],
            holdings=get_open_positions(portfolio),
        )
        summary += "\n\n" + get_portfolio_summary(portfolio)
        send_message(summary)

        if get_excel_exists():
            send_document(get_excel_path(), caption=f"Trade Log {date_str} {time_str}")

        # ── 10. Text log (append; multiple runs/day) ──
        os.makedirs(LOG_DIR, exist_ok=True)
        log_path = os.path.join(LOG_DIR, f"scan_{date_str}.txt")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\n{'='*50}\nRUN {date_str} {time_str} [{mode_tag}]\n{'='*50}\n")
            f.write(summary + "\n")
            for m in tg_events:
                f.write(m.replace("*", "").replace("`", "") + "\n")
        log(f"Log saved: {log_path}")

    except Exception as e:
        tb = traceback.format_exc()
        log(f"FATAL ERROR: {e}")
        log(tb)
        try:
            log_error("MAIN", e, tb)
        except Exception:
            pass
        try:
            send_message(f"🚨 *BOT ERROR* {date_str} {time_str}\n`{str(e)[:400]}`")
        except Exception:
            pass

    log("Done!")


if __name__ == "__main__":
    main()
