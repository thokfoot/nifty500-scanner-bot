"""
backtest_1m.py - 1-month backtest on REAL yfinance data.

Uses the EXACT production code paths:
  - scanner.is_volatile_down_close   (signal detection)
  - executor.BrokerSimulator         (D+1 limit fill + close-based exits)
  - config params                    (costs, Rs 10K/trade, max 8/day)

Window: last ~1 month of completed sessions.
Signal day D -> trade simulated on D+1 with finalize=True (full-day candles).
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
from executor import BrokerSimulator
from main import get_nifty500_tickers, _clean_yf

IST = pytz.timezone("Asia/Kolkata")
START_DATE = "2026-07-21"          # first SIGNAL day (~1 month back)


def build_signal(ticker, df, idx):
    """Identical to main.build_signal."""
    ok, details = is_volatile_down_close(df, idx, RANGE_PCT, CLOSE_POS_MAX, VOL_MULT)
    if not ok:
        return None
    prev_close = float(df.iloc[idx]["Close"])
    entry_price = prev_close * 0.992
    return {
        "ticker": ticker,
        "signal_date": df.index[idx].strftime("%Y-%m-%d"),
        "prev_close": round(prev_close, 2),
        "entry_price": round(entry_price, 2),
        "sl": round(entry_price * 0.975, 2),
        "t1": round(entry_price * 1.012, 2),
        "t2": round(entry_price * 1.028, 2),
        "details": details,
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


def main():
    t0 = time.time()
    print("=" * 60)
    print("BACKTEST: Nifty500 Volatile Down-Close | real data | prod logic")
    print(f"Signal window: {START_DATE} -> today-1")
    print("=" * 60)

    tickers = get_nifty500_tickers()
    daily, intra = download(tickers)

    # trading calendar from union of daily indexes
    all_dates = sorted({str(ts.date()) for df in daily.values() for ts in df.index})
    today_str = datetime.now(IST).strftime("%Y-%m-%d")

    # date->idx map per ticker (fast lookup)
    date_idx = {t: {str(ts.date()): i for i, ts in enumerate(df.index)}
                for t, df in daily.items()}

    broker = BrokerSimulator()
    trades, no_fills, bad_data, capped = [], 0, 0, 0
    signals_total, signal_days = 0, []

    for i in range(len(all_dates) - 1):
        d, nxt = all_dates[i], all_dates[i + 1]
        if d < START_DATE or nxt >= today_str:      # need COMPLETE D+1 data
            continue
        day_signals = []
        for ticker in sorted(date_idx.keys()):
            idx = date_idx[ticker].get(d)
            if idx is None or idx < 20:
                continue
            sig = build_signal(ticker, daily[ticker], idx)
            if sig:
                day_signals.append(sig)

        signals_total += len(day_signals)
        signal_days.append((d, len(day_signals)))
        taken = 0
        for sig in day_signals:
            day_df = intra.get(sig["ticker"], {}).get(nxt)
            if day_df is None:
                bad_data += 1
                continue
            if taken >= MAX_TRADES_PER_DAY:
                capped += 1
                continue
            outcome = broker.execute_signal(sig, day_df, finalize=True)
            if outcome["status"] == "EXECUTED":
                tr = outcome["trade"]
                tr["trade_date"] = nxt
                inv = tr["entry_price"] * tr["qty"]
                tr["investment"] = round(inv, 2)
                tr["pnl_amount"] = round(inv * tr["net_pnl_pct"] / 100.0, 2)
                trades.append(tr)
                taken += 1
            elif outcome["status"] == "NO_FILL":
                no_fills += 1

    # ── stats ──────────────────────────────────────────────────────
    trades.sort(key=lambda t: (t["trade_date"], t["ticker"]))
    n = len(trades)
    wins = [t for t in trades if t["net_pnl_pct"] > 0]
    losses = [t for t in trades if t["net_pnl_pct"] <= 0]
    total_rs = round(sum(t["pnl_amount"] for t in trades), 2)
    gross_rs = round(sum(t["entry_price"] * t["qty"] * t["gross_pnl_pct"] / 100.0 for t in trades), 2)

    # capital curve (chronological, cumulative on Rs 2L)
    cap, curve, peak, mdd = INITIAL_CAPITAL, [], 0.0, 0.0
    for t in trades:
        cap += t["pnl_amount"]
        curve.append(round(cap, 2))
        peak = max(peak, cap)
        mdd = min(mdd, (cap - peak) / peak * 100.0)

    by_reason = {}
    for t in trades:
        r = t["exit_reason"]
        b = by_reason.setdefault(r, {"n": 0, "rs": 0.0, "pcts": []})
        b["n"] += 1
        b["rs"] = round(b["rs"] + t["pnl_amount"], 2)
        b["pcts"].append(t["net_pnl_pct"])

    per_day = {}
    for t in trades:
        per_day.setdefault(t["trade_date"], []).append(t["pnl_amount"])
    day_pnl = {d: round(sum(v), 2) for d, v in per_day.items()}
    green_days = sum(1 for v in day_pnl.values() if v > 0)

    wr = round(len(wins) / n * 100, 1) if n else 0.0
    avg_win = round(sum(t["net_pnl_pct"] for t in wins) / len(wins), 3) if wins else 0
    avg_loss = round(sum(t["net_pnl_pct"] for t in losses) / len(losses), 3) if losses else 0
    gp = sum(t["net_pnl_pct"] for t in wins)
    gl = abs(sum(t["net_pnl_pct"] for t in losses))
    pf = round(gp / gl, 2) if gl else float("inf")

    print("\n" + "=" * 60)
    print(f"RESULTS  ({signal_days[0][0]} -> {signal_days[-1][0]} signals)")
    print("=" * 60)
    print(f"Signal days: {len(signal_days)} | Signals found: {signals_total} "
          f"({signals_total/max(len(signal_days),1):.1f}/day)")
    print(f"Trades taken: {n} | No-fill: {no_fills} | Capped(8/day): {capped} | BadData: {bad_data}")
    print("-" * 60)
    print(f"WINS/LOSSES : {len(wins)}W / {len(losses)}L  ({wr}% win rate)")
    print(f"Avg win     : {avg_win:+.2f}% | Avg loss: {avg_loss:+.2f}%")
    print(f"Profit factor: {pf} | Expectancy: "
          f"{round((avg_win*len(wins)+avg_loss*len(losses))/n,3) if n else 0}%/trade")
    print(f"Gross P&L   : Rs {gross_rs:+,.0f}")
    print(f"NET P&L     : Rs {total_rs:+,.0f}  ({total_rs/INITIAL_CAPITAL*100:+.2f}% on Rs 2L)")
    print(f"Capital     : Rs {INITIAL_CAPITAL:,.0f} -> Rs {cap:,.0f}")
    print(f"Max DD      : {mdd:.2f}% | Green days: {green_days}/{len(day_pnl)}")
    print("-" * 60)
    print("EXIT REASON BREAKDOWN:")
    for r, b in sorted(by_reason.items(), key=lambda kv: -kv[1]["n"]):
        avgp = sum(b["pcts"]) / len(b["pcts"])
        print(f"  {r:10s} {b['n']:3d} trades | avg {avgp:+.2f}% | Rs {b['rs']:+,.0f}")
    print("-" * 60)
    print("PER DAY P&L:")
    for d in sorted(day_pnl):
        flag = "+" if day_pnl[d] >= 0 else ""
        print(f"  {d}  Rs {flag}{day_pnl[d]:,.0f}  ({len(per_day[d])} trades)")

    print("\nALL TRADES:")
    for t in trades:
        print(f"  {t['trade_date']} {t['ticker']:11s} @{t['entry_price']:>9.2f} x{t['qty']:<4d}"
              f" -> {t['exit_reason']:9s} @{t['exit_price']:>9.2f}"
              f"  {t['net_pnl_pct']:+6.2f}%  Rs{t['pnl_amount']:+8,.0f}  [{t['holding_candles']}c]"
              f"{' T1+BE' if t['half_booked'] else ''}")

    out = {
        "window": f"{signal_days[0][0]} -> {signal_days[-1][0]}",
        "generated_at": datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S"),
        "summary": {
            "signal_days": len(signal_days), "signals_found": signals_total,
            "trades": n, "no_fill": no_fills, "capped": capped,
            "wins": len(wins), "losses": len(losses), "win_rate_pct": wr,
            "avg_win_pct": avg_win, "avg_loss_pct": avg_loss, "profit_factor": pf,
            "net_pnl_rs": total_rs, "return_pct": round(total_rs / INITIAL_CAPITAL * 100, 2),
            "final_capital": round(cap, 2), "max_drawdown_pct": round(mdd, 2),
            "green_days": green_days, "total_days": len(day_pnl),
        },
        "by_reason": {r: {"n": b["n"], "rs": b["rs"],
                          "avg_pct": round(sum(b["pcts"]) / len(b["pcts"]), 3)}
                      for r, b in by_reason.items()},
        "day_pnl": day_pnl,
        "trades": trades,
    }
    os.makedirs("data", exist_ok=True)
    with open("data/backtest_1m_results.json", "w") as f:
        json.dump(out, f, indent=2, default=str)
    print(f"\nSaved: data/backtest_1m_results.json | took {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
