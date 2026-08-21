"""
backtest_honest.py - FAIR test of the "buy recovery with VWAP confirmation" idea.

Fixes vs old v5 backtest (which showed 91.3% WR / +51%):
  BUG 1 (impossible fill): old entered at prev_close*0.992 while requiring
       price to break ABOVE opening high (~3% higher). Fixed: entry at the
       OPEN of the candle right after confirmation completes (realistic chase).
  BUG 2 (lookahead): old checked "2 of next 3 candles close above VWAP"
       BEFORE simulating the trade. Fixed: confirmation uses only COMPLETED
       candles; entry happens after it.
  BUG 3 (PnL accounting): old summed per-trade % as if full capital each
       trade. Fixed: Rs 10K fixed/trade, max 8/day (also show sum-of-% for
       apples-to-apples comparison).

Strategy tested (spirit of v5):
  Signal day D: volatile down-close scan (unchanged).
  Trade day D+1:
    - skip if day open > prev_close * 1.015 (gap filter)
    - opening_range_high = max(High of candle0, candle1)
    - confirmation: two consecutive 15m CLOSES above session VWAP,
      with the later close also >= opening_range_high (recovery + strength)
      searched from candle index 2 onward
    - entry at NEXT candle's Open (if confirmation at last candle -> no trade)
    - SL 1.3% | T1 1.2% (book 50%, SL->BE) | T2 2.1% | CLOSE-based exits
    - time exit at last candle close (15:15 data)
  Costs 0.30% round trip.
"""
import sys, os, time, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import pandas as pd
import pytz
import yfinance as yf

from config import (RANGE_PCT, CLOSE_POS_MAX, VOL_MULT,
                    MAX_TRADES_PER_DAY, INITIAL_CAPITAL, PER_TRADE_AMOUNT)
from scanner import is_volatile_down_close
from main import get_nifty500_tickers, _clean_yf

IST = pytz.timezone("Asia/Kolkata")
START_DATE = "2026-07-21"

SL_PCT, T1_PCT, T2_PCT = 1.3, 1.2, 2.1     # v5 grid-search best
COST_PCT = 0.30


def build_signal(ticker, df, idx):
    ok, details = is_volatile_down_close(df, idx, RANGE_PCT, CLOSE_POS_MAX, VOL_MULT)
    if not ok:
        return None
    prev_close = float(df.iloc[idx]["Close"])
    return {
        "ticker": ticker, "signal_date": df.index[idx].strftime("%Y-%m-%d"),
        "prev_close": round(prev_close, 2), "details": details,
    }


def download(tickers):
    daily, intra = {}, {}

    def _daily(t):
        try:
            return t, _clean_yf(yf.download(f"{t}.NS", period="1y", interval="1d",
                                            auto_adjust=True, progress=False, threads=False), 30)
        except Exception:
            return t, None

    def _intra(t):
        try:
            df = _clean_yf(yf.download(f"{t}.NS", period="60d", interval="15m",
                                       auto_adjust=True, progress=False, threads=False), 10)
            if df is None:
                return t, None
            return t, {str(d): g for d, g in df.groupby(df.index.date)}
        except Exception:
            return t, None

    def run(fn, universe, sink, label):
        pending = list(universe)
        for attempt in (1, 2):
            if not pending:
                break
            done = set()
            with ThreadPoolExecutor(max_workers=8) as pool:
                futs = {pool.submit(fn, t): t for t in pending}
                for fut in as_completed(futs):
                    t, res = fut.result()
                    if res is not None:
                        sink[t] = res
                        done.add(t)
            pending = [t for t in pending if t not in done]
        print(f"[{label}] {len(sink)}/{len(universe)} ok")

    run(_daily, tickers, daily, "daily")
    run(_intra, list(daily.keys()), intra, "15m")
    return daily, intra


def simulate_trade(sig, df):
    """Honest D+1 simulation. Returns trade dict or None."""
    if df is None or len(df) < 8:
        return None
    prev_close = float(sig["prev_close"])

    # gap filter
    if float(df.iloc[0]["Open"]) > prev_close * 1.015:
        return None

    ohigh = max(float(df.iloc[0]["High"]), float(df.iloc[1]["High"]))

    # running session VWAP
    typical = (df["High"] + df["Low"] + df["Close"]) / 3.0
    tpv = (typical * df["Volume"]).cumsum()
    vv = df["Volume"].cumsum().replace(0, pd.NA)
    vwap = tpv / vv

    closes = df["Close"].values
    highs = df["High"].values

    # confirmation: two consecutive closes above VWAP, latest also >= opening high
    entry_idx = None
    for i in range(3, len(df)):
        try:
            c_ok = closes[i] >= float(vwap.iloc[i]) and closes[i - 1] >= float(vwap.iloc[i - 1])
        except Exception:
            c_ok = False
        if c_ok and closes[i] >= ohigh and highs[i] >= ohigh:
            entry_idx = i + 1                      # enter NEXT candle
            break
    if entry_idx is None or entry_idx >= len(df):
        return None                                 # confirmation too late

    entry = float(df.iloc[entry_idx]["Open"])       # REALISTIC fill: next open
    sl = entry * (1 - SL_PCT / 100)
    t1 = entry * (1 + T1_PCT / 100)
    t2 = entry * (1 + T2_PCT / 100)

    pnl, half_booked, reason, exit_px = 0.0, False, None, None
    for i in range(entry_idx, len(df)):
        cl = float(df.iloc[i]["Close"])
        if not half_booked:
            if cl <= sl:
                reason, exit_px = "SL", sl
                pnl = (sl - entry) / entry * 100
                break
            if cl >= t2:
                reason, exit_px = "T2", t2
                pnl = (t2 - entry) / entry * 100
                break
            if cl >= t1:
                half_booked = True
                pnl = 0.5 * (t1 - entry) / entry * 100
                sl = entry                          # BE
                continue
        else:
            if cl <= sl:
                reason, exit_px = "T1_BE", sl
                break                               # banked stays
            if cl >= t2:
                reason, exit_px = "T2", t2
                pnl += 0.5 * (t2 - entry) / entry * 100
                break
    if reason is None:
        reason = "TIME_EXIT"
        exit_px = float(df.iloc[-1]["Close"])
        pnl += 0.5 * (exit_px - entry) / entry * 100 if half_booked \
            else (exit_px - entry) / entry * 100

    gross = round(pnl, 3)
    qty = max(int(PER_TRADE_AMOUNT / entry), 1)
    return {
        "ticker": sig["ticker"], "signal_date": sig["signal_date"],
        "trade_date": str(df.index[0])[:10],
        "entry": round(entry, 2), "exit": round(exit_px, 2),
        "exit_reason": reason, "qty": qty,
        "gross_pct": gross, "net_pct": round(gross - COST_PCT, 3),
        "pnl_amount": round(PER_TRADE_AMOUNT * (gross - COST_PCT) / 100, 2),
        "half_booked": half_booked,
    }


def main():
    t0 = time.time()
    print("=" * 60)
    print("HONEST BACKTEST: recovery+VWAP-confirm entry, no lookahead")
    print("=" * 60)
    tickers = get_nifty500_tickers()
    daily, intra = download(tickers)

    all_dates = sorted({str(ts.date()) for df in daily.values() for ts in df.index})
    today_str = datetime.now(IST).strftime("%Y-%m-%d")
    date_idx = {t: {str(ts.date()): i for i, ts in enumerate(df.index)}
                for t, df in daily.items()}

    broker_trades, skipped_confirm, skipped_gap = [], 0, 0
    signals_total = 0

    for i in range(len(all_dates) - 1):
        d, nxt = all_dates[i], all_dates[i + 1]
        if d < START_DATE or nxt >= today_str:
            continue
        day_sigs = []
        for ticker in sorted(date_idx.keys()):
            idx = date_idx[ticker].get(d)
            if idx is None or idx < 20:
                continue
            s = build_signal(ticker, daily[ticker], idx)
            if s:
                day_sigs.append(s)
        signals_total += len(day_sigs)

        taken = 0
        for sig in day_sigs:
            if taken >= MAX_TRADES_PER_DAY:
                break
            df = intra.get(sig["ticker"], {}).get(nxt)
            tr = simulate_trade(sig, df) if df is not None else None
            if tr is None:
                skipped_confirm += 1
                continue
            broker_trades.append(tr)
            taken += 1

    n = len(broker_trades)
    wins = [t for t in broker_trades if t["net_pct"] > 0]
    losses = [t for t in broker_trades if t["net_pct"] <= 0]
    total_rs = round(sum(t["pnl_amount"] for t in broker_trades), 2)
    sum_pct = round(sum(t["net_pct"] for t in broker_trades), 2)
    wr = round(len(wins) / n * 100, 1) if n else 0
    aw = round(sum(t["net_pct"] for t in wins) / len(wins), 3) if wins else 0
    al = round(sum(t["net_pct"] for t in losses) / len(losses), 3) if losses else 0
    gp = sum(t["net_pct"] for t in wins)
    gl = abs(sum(t["net_pct"] for t in losses))
    pf = round(gp / gl, 2) if gl else float("inf")

    cap, peak, mdd = INITIAL_CAPITAL, INITIAL_CAPITAL, 0.0
    for t in broker_trades:
        cap += t["pnl_amount"]
        peak = max(peak, cap)
        mdd = min(mdd, (cap - peak) / peak * 100)

    by_reason = {}
    for t in broker_trades:
        b = by_reason.setdefault(t["exit_reason"], {"n": 0, "rs": 0.0})
        b["n"] += 1
        b["rs"] += t["pnl_amount"]

    print("\n" + "=" * 60)
    print("HONEST RESULTS (same window, same signals)")
    print("=" * 60)
    print(f"Signals: {signals_total} | Confirmation entries: {n} | "
          f"no-confirm/gap skip: {skipped_confirm}")
    print(f"WR: {wr}% ({len(wins)}W/{len(losses)}L) | AvgWin {aw:+.2f}% | AvgLoss {al:+.2f}% | PF {pf}")
    print(f"Sum-of-% (old accounting): {sum_pct:+.1f}%")
    print(f"REAL P&L @Rs10K/trade: Rs {total_rs:+,.0f} "
          f"({total_rs / INITIAL_CAPITAL * 100:+.2f}% on Rs 2L)")
    print(f"Capital: Rs {cap:,.0f} | MaxDD {mdd:.2f}%")
    print("\nBy reason:")
    for r, b in sorted(by_reason.items(), key=lambda kv: -kv[1]["n"]):
        print(f"  {r:10s} {b['n']:3d} | Rs {b['rs']:+,.0f}")
    print("\nTrades:")
    for t in broker_trades:
        print(f"  {t['trade_date']} {t['ticker']:11s} @{t['entry']:>9.2f} -> "
              f"{t['exit_reason']:9s} @{t['exit']:>9.2f}  {t['net_pct']:+6.2f}%  "
              f"Rs{t['pnl_amount']:+7,.0f}")

    out = {"summary": {"signals": signals_total, "trades": n, "win_rate": wr,
                       "avg_win": aw, "avg_loss": al, "pf": pf,
                       "sum_pct_old_accounting": sum_pct,
                       "real_pnl_rs": total_rs, "return_pct": round(total_rs / INITIAL_CAPITAL * 100, 2),
                       "max_dd_pct": round(mdd, 2)},
           "trades": broker_trades}
    with open("data/backtest_honest_results.json", "w") as f:
        json.dump(out, f, indent=2, default=str)
    print(f"\nSaved data/backtest_honest_results.json | {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
