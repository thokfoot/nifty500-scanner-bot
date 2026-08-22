"""Frozen V4 out-of-sample validation; deliberately contains no tuning grid."""
from __future__ import annotations

import csv
import json
import math
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from dataclasses import asdict
from pathlib import Path

import pandas as pd
import yfinance as yf

from full_tune_v4_full_strategy import Params, simulate

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
FROZEN = DATA / "v4_full_results.json"
DAILY_PATH = DATA / "v4_30d_daily.csv"
INTRADAY_PATH = DATA / "v4_30d_5m.csv"
RESULT_PATH = DATA / "v4_30d_validation_results.json"
REPORT_PATH = DATA / "v4_30d_validation_report.md"
PROOF_PATH = DATA / "v4_30d_proof.txt"


def frozen_params():
    payload = json.loads(FROZEN.read_text(encoding="utf-8"))
    finalists = payload.get("top10", [])
    if not finalists:
        raise ValueError("v4_full_results.json has no frozen finalists")
    # The original run did not persist BEST_ROBUST/BEST_RAW/BEST_CONSERVATIVE
    # labels. The first finalist is therefore the deterministic primary.
    primary = finalists[0]
    return Params(**primary["params"]), finalists[:3], payload


def clean(frame):
    if frame is None or frame.empty:
        return None
    if isinstance(frame.columns, pd.MultiIndex):
        frame.columns = frame.columns.get_level_values(0)
    needed = ["Open", "High", "Low", "Close", "Volume"]
    if not all(column in frame for column in needed):
        return None
    return frame[needed].dropna()


def fetch_data():
    from main import get_nifty500_tickers

    tickers = [f"{ticker}.NS" for ticker in get_nifty500_tickers()]
    today = datetime.now().date()

    def get_daily(ticker):
        return ticker, clean(yf.download(ticker, start=today - timedelta(days=58),
                                          end=today + timedelta(days=1), interval="1d",
                                          auto_adjust=True, progress=False, threads=False))

    def get_intraday(ticker):
        # Yahoo allows 5m history for roughly 60 days, unlike 1m history.
        return ticker, clean(yf.download(ticker, start=today - timedelta(days=58),
                                          end=today + timedelta(days=1), interval="5m",
                                          auto_adjust=True, progress=False, threads=False))

    daily, intraday = {}, {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        jobs = [pool.submit(get_daily, ticker) for ticker in tickers]
        for job in as_completed(jobs):
            ticker, frame = job.result()
            if frame is not None:
                daily[ticker] = frame
    with ThreadPoolExecutor(max_workers=8) as pool:
        jobs = [pool.submit(get_intraday, ticker) for ticker in daily]
        for job in as_completed(jobs):
            ticker, frame = job.result()
            if frame is not None:
                intraday[ticker] = frame
    if not intraday:
        raise RuntimeError("Yahoo returned no 5m validation candles")

    sessions = sorted({str(index.date()) for frame in intraday.values() for index in frame.index})
    if len(sessions) < 30:
        raise RuntimeError(f"Yahoo returned only {len(sessions)} 5m sessions; need 30")
    sessions = sessions[-30:]
    daily_sessions = set(sorted({str(index.date()) for frame in daily.values() for index in frame.index})[-60:])
    DATA.mkdir(exist_ok=True)
    with DAILY_PATH.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["date", "ticker", "open", "high", "low", "close", "volume"])
        for ticker, frame in sorted(daily.items()):
            for index, row in frame.iterrows():
                if str(index.date()) in daily_sessions:
                    writer.writerow([str(index.date()), ticker, row.Open, row.High, row.Low, row.Close, row.Volume])
    with INTRADAY_PATH.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["date", "ticker", "time", "open", "high", "low", "close", "volume"])
        for ticker, frame in sorted(intraday.items()):
            for index, row in frame.iterrows():
                if str(index.date()) in sessions:
                    writer.writerow([str(index.date()), ticker, index.strftime("%H:%M:%S"), row.Open, row.High, row.Low, row.Close, row.Volume])
    return "5m", sessions


def load_csv():
    daily = defaultdict(dict)
    with DAILY_PATH.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            daily[row["ticker"]][row["date"]] = {key: float(row[key]) for key in ("open", "high", "low", "close", "volume")}
    bars = defaultdict(list)
    with INTRADAY_PATH.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            values = {key: float(row[key]) for key in ("open", "high", "low", "close", "volume")}
            from full_tune_v4_full_strategy import Bar
            bars[(row["ticker"], row["date"])].append(Bar(row["ticker"], row["date"], row["time"], **values))
    for key in bars:
        bars[key].sort(key=lambda bar: bar.minute)
    return daily, bars


def stats(trades):
    values = [trade["net_pct"] for trade in trades]
    wins = [value for value in values if value > 0]
    losses = [value for value in values if value <= 0]
    curve, peak, max_dd = 0.0, 0.0, 0.0
    equity = []
    for value in values:
        curve += value * 200000 / 100
        peak = max(peak, curve)
        max_dd = min(max_dd, curve - peak)
        equity.append(round(200000 + curve, 2))
    day_pnl = defaultdict(float)
    for trade in trades:
        day_pnl[trade["day"]] += trade["net_pct"] * 200000 / 100
    pf = sum(wins) / abs(sum(losses)) if losses else None
    return {"trades": len(values), "expectancy_pct": sum(values) / len(values) if values else 0,
            "profit_factor": pf, "max_dd_rs": round(max_dd, 2),
            "max_dd_pct": round(abs(max_dd) / 200000 * 100, 4),
            "worst_day_rs": round(min(day_pnl.values(), default=0), 2),
            "max_consec_loss": max((len(list(group)) for negative, group in __import__("itertools").groupby(values, lambda x: x <= 0) if negative), default=0),
            "winrate_pct": len(wins) / len(values) * 100 if values else 0,
            "avg_win_pct": sum(wins) / len(wins) if wins else 0,
            "avg_loss_pct": sum(losses) / len(losses) if losses else 0,
            "equity_curve": equity, "day_pnl_rs": dict(day_pnl)}


def main():
    params, top3, prior = frozen_params()
    if not DAILY_PATH.exists() or not INTRADAY_PATH.exists():
        interval, sessions = fetch_data()
    else:
        interval, sessions = "5m", sorted({line.split(",", 1)[0] for line in INTRADAY_PATH.read_text(encoding="utf-8").splitlines()[1:]})
    daily, bars = load_csv()
    trades, accounting = [], Counter()
    for ticker in sorted(daily):
        for day in sessions:
            accounting["generated"] += 1
            result = simulate(ticker, day, bars.get((ticker, day), []), daily, params, (3, -.06, -.02, .01, 1.2, None))
            if result is None:
                accounting["blocked"] += 1
                continue
            accounting["executed"] += 1
            accounting[result["reason"].lower().replace("/", "_")] += 1
            result["pnl_rs"] = result["net_pct"] * 200000 / 100
            trades.append(result)
    summary = stats(trades)
    for key in ("generated", "blocked_gap", "blocked_pos", "executed", "cvd_exits", "sl", "t1", "t2", "be", "time"):
        accounting.setdefault(key, 0)
    summary["signals_accounting"] = dict(accounting)
    output = {"frozen_primary": asdict(params), "interval": interval, "sessions": sessions,
              "data_period": [sessions[0], sessions[-1]], "summary": summary,
              "trades": trades, "prior_v4_test_top3": top3, "prior_stage1_count": prior.get("stage1_count")}
    RESULT_PATH.write_text(json.dumps(output, indent=2, default=str), encoding="utf-8")
    comparison = top3[0].get("stats", {}) if top3 else {}
    verdict = "INCONCLUSIVE DUE TO DATA LIMIT" if interval != "1m" else ("EDGE" if summary["expectancy_pct"] > 0 else "NO EDGE")
    REPORT_PATH.write_text("# V4 30-Day Unseen Validation\n\n"
        f"- Frozen primary: `{json.dumps(asdict(params), sort_keys=True)}`\n"
        f"- Frozen source: first finalist; explicit robust/raw/conservative labels were absent\n"
        f"- Data period: `{sessions[0]} -> {sessions[-1]}`\n- Interval used: `{interval}` (Yahoo 1m limit fallback)\n"
        f"- Trades: `{summary['trades']}` | Expectancy: `{summary['expectancy_pct']:.4f}%` | PF: `{summary['profit_factor']}`\n"
        f"- MaxDD: `Rs {summary['max_dd_rs']:.2f}` ({summary['max_dd_pct']:.4f}%) | Worst day: `Rs {summary['worst_day_rs']:.2f}`\n"
        f"- Prior V4 test finalist expectancy: `{comparison.get('expectancy')}`\n\n"
        f"## Verdict\n`{verdict}`\n\n30 true unseen 1-minute sessions require forward collection or a provider such as TrueData/Upstox.\n", encoding="utf-8")
    with PROOF_PATH.open("w", encoding="utf-8") as fh:
        fh.write("V4 30-day validation proof; raw OHLCV samples from generated real candles.\n")
        with INTRADAY_PATH.open(newline="", encoding="utf-8") as source:
            for index, row in enumerate(csv.DictReader(source)):
                if index >= 50: break
                fh.write(json.dumps(row) + "\n")
    print(json.dumps({"interval": interval, "sessions": len(sessions), "trades": summary["trades"], "verdict": verdict}, indent=2))


if __name__ == "__main__":
    main()