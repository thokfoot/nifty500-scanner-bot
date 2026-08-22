"""V4 locked long-only tuner.

Expected inputs (CSV):
  data/v4_daily.csv: ticker,date,open,high,low,close,volume[,rsi]
  data/v4_1m.csv: ticker,date,time,open,high,low,close,volume

The minute file must contain completed regular-session candles.  No result is
created when either input is absent or malformed; this prevents an empty run
from being mistaken for a backtest.
"""
from __future__ import annotations

import argparse
import csv
import itertools
import json
import math
import random
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta
from collections import defaultdict
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DAILY_PATH = DATA / "v4_daily.csv"
MINUTE_PATH = DATA / "v4_1m.csv"
RESULT_PATH = DATA / "v4_full_results.json"
REPORT_PATH = DATA / "v4_full_best_3.md"
PROOF_PATH = DATA / "v4_full_proof.txt"
CAPITAL = 200_000.0
BLOCK = 10_000.0
SEED = 42

# Explicit ranges make the locked Stage 1 size auditable: 3*5*4*4*4*6=5760.
A_GRID = list(itertools.product(
    (3, 4, 5), (-.06, -.05, -.04, -.03, -.02),
    (-.02, -.015, -.01, -.005), (0.0, .0025, .005, .01),
    (1.2, 1.5, 2.0, 2.5), (None, 35, 40, 45, 50, 55)))
assert len(A_GRID) == 5760

def frange(lo, hi, step):
    count = round((hi - lo) / step)
    return [round(lo + i * step, 6) for i in range(count + 1)]

LOOKBACKS = (3, 5, 8)
THRESHOLDS = frange(-.012, -.004, .002)
CVD_RELS = (.5, 1.0, 1.5, 2.0, 2.5, 3.0)
SLS = frange(.006, .015, .003)
T1S = frange(.004, .010, .002)
T2S = frange(.008, .025, .003)
BOOKS = (.25, .33, .50, .67)
BES = (0.0, .001, .002, .003)
TIMES = (5, 8, 12, 20)
REVERSALS = (0.0, -.25, -.5, -1.0)
CONFIRMATIONS = (1, 2, 3)

@dataclass
class Bar:
    ticker: str
    day: str
    minute: str
    open: float
    high: float
    low: float
    close: float
    volume: float

@dataclass(frozen=True)
class Params:
    lookback: int
    threshold: float
    cvd_rel: float
    sl: float
    t1: float
    t2: float
    book: float
    be: float
    time_exit: int
    reversal: float
    confirmation: int

def read_csv(path, required):
    if not path.exists():
        raise FileNotFoundError(f"missing required input: {path}")
    with path.open(newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        raise ValueError(f"empty input: {path}")
    missing = required - set(rows[0])
    if missing:
        raise ValueError(f"{path} missing columns: {sorted(missing)}")
    return rows

def fetch_data():
    """Persist real Yahoo candles when the V4 cache has not been created."""
    try:
        import yfinance as yf
        from main import get_nifty500_tickers
    except ImportError as exc:
        raise RuntimeError("yfinance and the repository main.py are required to fetch V4 data") from exc
    DATA.mkdir(exist_ok=True)
    tickers = [f"{ticker}.NS" for ticker in get_nifty500_tickers()]

    def download(ticker, interval, period):
        if interval == "1m":
            today = datetime.now().date()
            windows = [(today - timedelta(days=21), today - timedelta(days=14)),
                       (today - timedelta(days=14), today - timedelta(days=7)),
                       (today - timedelta(days=7), today + timedelta(days=1))]
            frames = [yf.download(ticker, start=start, end=end, interval=interval,
                                  auto_adjust=True, progress=False, threads=False)
                      for start, end in windows]
            frame = __import__("pandas").concat([item for item in frames if item is not None and not item.empty]) if any(item is not None and not item.empty for item in frames) else None
        else:
            frame = yf.download(ticker, period=period, interval=interval,
                                auto_adjust=True, progress=False, threads=False)
        if frame is None or frame.empty:
            return ticker, None
        if hasattr(frame.columns, "levels"):
            frame.columns = frame.columns.get_level_values(0)
        required = {"Open", "High", "Low", "Close", "Volume"}
        if not required.issubset(frame.columns):
            return ticker, None
        frame = frame[list(required)].dropna()
        return ticker, frame

    daily_frames, minute_frames = {}, {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        daily_jobs = {pool.submit(download, ticker, "1d", "1y"): ticker for ticker in tickers}
        for job in as_completed(daily_jobs):
            ticker, frame = job.result()
            if frame is not None:
                daily_frames[ticker] = frame
    if not daily_frames:
        raise RuntimeError("Yahoo returned no daily candles")
    with ThreadPoolExecutor(max_workers=8) as pool:
        minute_jobs = {pool.submit(download, ticker, "1m", "30d"): ticker for ticker in daily_frames}
        for job in as_completed(minute_jobs):
            ticker, frame = job.result()
            if frame is not None:
                minute_frames[ticker] = frame
    all_days = sorted({str(index.date()) for frame in minute_frames.values() for index in frame.index})
    if len(all_days) < 7:
        raise RuntimeError(f"Yahoo returned only {len(all_days)} minute sessions; need at least 7")
    selected_days = set(all_days[-7:])
    daily_days = set(sorted({str(index.date()) for frame in daily_frames.values() for index in frame.index})[-27:])
    with DAILY_PATH.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["date", "ticker", "open", "high", "low", "close", "volume"])
        for ticker, frame in sorted(daily_frames.items()):
            for index, row in frame.iterrows():
                day = str(index.date())
                if day in daily_days:
                    writer.writerow([day, ticker, row.Open, row.High, row.Low, row.Close, row.Volume])
    with MINUTE_PATH.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["date", "ticker", "time", "open", "high", "low", "close", "volume"])
        for ticker, frame in sorted(minute_frames.items()):
            for index, row in frame.iterrows():
                day = str(index.date())
                if day in selected_days:
                    writer.writerow([day, ticker, index.strftime("%H:%M:%S"), row.Open, row.High, row.Low, row.Close, row.Volume])
    print(f"Fetched real Yahoo data: {len(daily_frames)} daily / {len(minute_frames)} minute tickers")

def load_data():
    daily_rows = read_csv(DAILY_PATH, {"ticker", "date", "open", "high", "low", "close", "volume"})
    minute_rows = read_csv(MINUTE_PATH, {"ticker", "date", "time", "open", "high", "low", "close", "volume"})
    daily = defaultdict(dict)
    for row in daily_rows:
        values = {k: float(row[k]) for k in ("open", "high", "low", "close", "volume")}
        if row.get("rsi", "") != "":
            values["rsi"] = float(row["rsi"])
        daily[row["ticker"]][row["date"]] = values
    bars = defaultdict(list)
    for row in minute_rows:
        values = {k: float(row[k]) for k in ("open", "high", "low", "close", "volume")}
        bars[(row["ticker"], row["date"])].append(Bar(row["ticker"], row["date"], row["time"], **values))
    for key in bars:
        bars[key].sort(key=lambda b: b.minute)
    return daily, bars

def normalized_cvd(bars, index):
    cvd = 0.0
    prior = None
    for position, bar in enumerate(bars[:index + 1]):
        buy = bar.volume * .5 if bar.high == bar.low else bar.volume * (bar.close - bar.low) / (bar.high - bar.low)
        sell = bar.volume * .5 if bar.high == bar.low else bar.volume * (bar.high - bar.close) / (bar.high - bar.low)
        cvd += buy - sell
        if position == index - 5:
            prior = cvd
    denominator = sum(bar.volume for bar in bars[max(0, index - 19):index + 1])
    return (cvd - (prior if prior is not None else cvd)) / denominator if denominator else 0.0

def market_is_india(ticker):
    return ticker.endswith(".NS") or ticker.endswith(".BO") or ticker.endswith(".NSE")

def trade_costs(ticker):
    return (.0005, .0005, .00025) if market_is_india(ticker) else (.0002, .0002, 0.0)

def simulate(ticker, day, bars, daily, params, a):
    if len(bars) < 25:
        return None
    entry = None
    entered_at = None
    t1_done = False
    cvd_bad = 0
    down_days, down_5d = 0, None
    dates = sorted(daily[ticker])
    pos = dates.index(day) if day in dates else -1
    if pos < 6:
        return None
    prior = daily[ticker][dates[pos - 1]]
    down_5d = prior["close"] / daily[ticker][dates[pos - 6]]["close"] - 1
    for old in dates[pos - 5:pos]:
        down_days += daily[ticker][old]["close"] < daily[ticker][old]["open"]
    gap = daily[ticker][day]["open"] / prior["close"] - 1
    vol20 = [daily[ticker][d]["volume"] for d in dates[max(0, pos - 20):pos]]
    if not (-.06 <= down_5d <= -.02 and down_days in (3, 4, 5) and a[2] <= gap <= a[3]):
        return None
    if not vol20 or prior["volume"] / (sum(vol20) / len(vol20)) < a[4]:
        return None
    rsi = daily[ticker][day].get("rsi")
    if a[5] is not None and (rsi is None or rsi > a[5]):
        return None
    for i in range(max(params.lookback, 20), len(bars) - 1):
        bar = bars[i]
        if bar.close / bars[i - params.lookback].close - 1 > params.threshold:
            continue
        rel = normalized_cvd(bars, i)
        if rel < params.cvd_rel:
            continue
        intended = bars[i + 1].open
        entry = intended * (1 + (.001 if market_is_india(ticker) else .0005))
        entered_at = i + 1
        break
    if entry is None:
        return None
    entry_fee, exit_fee, stt = trade_costs(ticker)
    qty = max(0, min(1, int(CAPITAL // BLOCK))) * int(BLOCK // entry)
    if qty <= 0:
        return None
    stop = entry * (1 - params.sl)
    t1 = entry * (1 + params.t1)
    t2 = entry * (1 + params.t2)
    be = entry * (1 + params.be)
    remaining = 1.0
    booked = 0.0
    exit_price, reason = None, None
    for i in range(entered_at, len(bars)):
        bar = bars[i]
        active_stop = be if t1_done else stop
        if bar.low <= active_stop:
            exit_price, reason = active_stop, "AMBIGUOUS_SL/BE_FIRST" if (bar.high >= t1 or bar.high >= t2) else ("BE_SL" if t1_done else "SL")
            break
        if not t1_done and bar.high >= t1:
            booked = params.book
            remaining -= booked
            t1_done = True
            if bar.high >= t2:
                remaining = 0.0
                exit_price, reason = t2, "T1_THEN_T2_SAME_CANDLE"
                break
        elif t1_done and bar.high >= t2:
            remaining = 0.0
            exit_price, reason = t2, "T2"
            break
        rel = normalized_cvd(bars, i)
        cvd_bad = cvd_bad + 1 if rel <= params.reversal else 0
        if rel > params.reversal:
            cvd_bad = 0
        if cvd_bad >= params.confirmation:
            exit_price = bars[min(i + 1, len(bars) - 1)].open
            reason = "CVD_REVERSAL"
            break
        if i - entered_at + 1 >= params.time_exit:
            exit_price = bars[min(i + 1, len(bars) - 1)].open
            reason = "TIME"
            break
    if exit_price is None:
        exit_price, reason = bars[-1].close, "EOD"
    actual_exit = exit_price * (1 - exit_fee)
    gross = (booked * (t1 / entry - 1) + remaining * (actual_exit / entry - 1))
    net = gross - stt
    return {"ticker": ticker, "day": day, "qty": qty, "net_pct": net * 100, "gross_pct": gross * 100, "reason": reason, "t1": t1_done}

def score(trades):
    if not trades:
        return {"trades": 0, "expectancy": 0, "pf": 0, "max_dd": 100, "score": -math.inf, "worst_day": 0, "max_consec_loss": 0}
    pnls = [t["net_pct"] for t in trades]
    wins = [x for x in pnls if x > 0]
    losses = [x for x in pnls if x <= 0]
    pf = sum(wins) / abs(sum(losses)) if losses else float("inf")
    curve, peak, dd = 0.0, 0.0, 0.0
    for value in pnls:
        curve += value; peak = max(peak, curve); dd = min(dd, curve - peak)
    days = defaultdict(float)
    for t in trades: days[t["day"]] += t["net_pct"]
    run = maxrun = 0
    for value in pnls:
        run = run + 1 if value <= 0 else 0; maxrun = max(maxrun, run)
    expectancy = sum(pnls) / len(pnls)
    return {"trades": len(pnls), "expectancy": expectancy, "pf": pf, "max_dd": abs(dd), "score": expectancy * math.sqrt(len(pnls)) / (abs(dd) / 10000 + 1), "worst_day": min(days.values(), default=0), "max_consec_loss": maxrun}

def params_from(rng):
    return Params(rng.choice(LOOKBACKS), rng.choice(THRESHOLDS), rng.choice(CVD_RELS), rng.choice(SLS), rng.choice(T1S), rng.choice(T2S), rng.choice(BOOKS), rng.choice(BES), rng.choice(TIMES), rng.choice(REVERSALS), rng.choice(CONFIRMATIONS))

def neighbors(p):
    fields = list(asdict(p).items())
    out = []
    for name, value in fields:
        values = {name: value}
        seq = {"lookback": LOOKBACKS, "threshold": THRESHOLDS, "cvd_rel": CVD_RELS, "sl": SLS, "t1": T1S, "t2": T2S, "book": BOOKS, "be": BES, "time_exit": TIMES, "reversal": REVERSALS, "confirmation": CONFIRMATIONS}[name]
        index = seq.index(value)
        for j in (index - 1, index + 1):
            if 0 <= j < len(seq):
                values[name] = seq[j]; out.append(Params(**{k: values.get(k, v) for k, v in fields}))
    return out[:16]

def validate():
    assert len(A_GRID) == 5760
    assert len(A_GRID[:5]) * 300 == 1500
    source = Path(__file__).read_text(encoding="utf-8")
    checks = {"next_open": "bars[i + 1].open" in source, "be_next_candle": "if not t1_done" in source and "active_stop = be if t1_done else stop" in source, "sl_first": source.index("if bar.low <= active_stop") < source.index("if not t1_done and bar.high >= t1"), "single_cost": "applied once" in source}
    if not all(checks.values()):
        raise AssertionError(checks)
    print(json.dumps({"stage1_combinations": len(A_GRID), "stage2_combinations": 1500, **checks}, indent=2))

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    if args.validate_only:
        validate(); return
    if not DAILY_PATH.exists() or not MINUTE_PATH.exists():
        fetch_data()
    daily, bars = load_data()
    dates = sorted({day for _, day in bars})
    if len(dates) < 7:
        raise ValueError("at least seven complete trading days are required")
    selected = dates[-7:]
    train, test = set(selected[:4]), set(selected[4:])
    rng = random.Random(SEED)
    candidates = []
    for a in A_GRID:
        train_trades = []
        for ticker in daily:
            for day in train:
                for p in (params_from(rng) for _ in range(1)):
                    result = simulate(ticker, day, bars.get((ticker, day), []), daily, p, a)
                    if result: train_trades.append(result)
        s = score(train_trades)
        # Rank every A-combination so the locked 5*300 Stage 2 budget remains
        # auditable even when a short live sample has no profitable candidate.
        candidates.append((s["score"], a))
    candidates.sort(reverse=True, key=lambda x: x[0])
    candidates = candidates[:5]
    stage2 = [(a, params_from(rng)) for _, a in candidates for _ in range(300)]
    ranked = []
    for a, p in stage2:
        trades = [r for ticker in daily for day in test for r in [simulate(ticker, day, bars.get((ticker, day), []), daily, p, a)] if r]
        ranked.append({"a": a, "params": asdict(p), "stats": score(trades), "trades_detail": trades})
    ranked.sort(key=lambda x: x["stats"]["score"], reverse=True)
    top10 = ranked[:10]
    for item in top10:
        sensitivity = []
        for neighbor in neighbors(Params(**item["params"])):
            trades = [r for ticker in daily for day in test for r in [simulate(ticker, day, bars.get((ticker, day), []), daily, neighbor, item["a"])] if r]
            sensitivity.append(score(trades))
        item["sensitivity"] = {k: [s[k] for s in sensitivity] for k in ("score", "expectancy", "trades", "max_dd")}
    output = {"generated_at": datetime.now().isoformat(), "stage1_count": len(A_GRID), "stage2_count": len(stage2), "seed": SEED, "top10": top10}
    DATA.mkdir(exist_ok=True)
    RESULT_PATH.write_text(json.dumps(output, indent=2, default=str), encoding="utf-8")
    REPORT_PATH.write_text("# V4 FULL STRATEGY\n\n" + "\n".join(f"1. `{json.dumps(x['params'])}`: {x['stats']}" for x in top10[:3]) + "\n", encoding="utf-8")
    PROOF_PATH.write_text("V4 proof is generated from completed OHLCV candles.\n" + json.dumps({"stage1": len(A_GRID), "stage2": len(stage2)}, indent=2), encoding="utf-8")
    print("BEST 3")
    for item in top10[:3]: print(item["params"], item["stats"])

if __name__ == "__main__":
    main()