"""
verify_claims.py - cross-check honest backtest against RAW candles + variants.

1. MANUAL VERIFY: re-simulate 3 trades candle-by-candle from fresh yfinance
   download and compare with stored results.
2. VARIANTS: does the conclusion hold under different entry rules?
   V1 = as tested (2 closes>VWAP + close>=opening-high, enter next open)
   V2 = 2 closes>VWAP only (no opening-high condition), enter next open
   V3 = pure breakout: first High >= opening-high after candle 1,
        enter AT trigger (no VWAP), exits from that candle's close onward
3. FAKE-FILL PROOF: ACUTAAS 2026-07-27 - show raw candles proving the old
   backtest's entry price (3233.42) was unreachable after the trigger fired.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import json
import pandas as pd
import pytz
import yfinance as yf

IST = pytz.timezone("Asia/Kolkata")
SL_PCT, T1_PCT, T2_PCT, COST_PCT = 1.3, 1.2, 2.1, 0.30


def get_15m(ticker, date_str):
    df = yf.download(f"{ticker}.NS", period="60d", interval="15m",
                     auto_adjust=True, progress=False, threads=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.index = pd.to_datetime(df.index)
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
    return df[df.index.strftime("%Y-%m-%d") == date_str]


def simulate(df, mode):
    """mode: 'V1' | 'V2' | 'V3'"""
    if df is None or len(df) < 8:
        return None
    ohigh = max(float(df.iloc[0]["High"]), float(df.iloc[1]["High"]))
    typical = (df["High"] + df["Low"] + df["Close"]) / 3.0
    vwap = ((typical * df["Volume"]).cumsum() /
            df["Volume"].cumsum().replace(0, pd.NA))
    closes = df["Close"].values
    highs = df["High"].values

    entry_idx = entry_px = None
    if mode in ("V1", "V2"):
        for i in range(3, len(df)):
            try:
                c_ok = closes[i] >= float(vwap.iloc[i]) and closes[i-1] >= float(vwap.iloc[i-1])
            except Exception:
                c_ok = False
            ok = c_ok and (closes[i] >= ohigh if mode == "V1" else True)
            if ok:
                entry_idx = i + 1
                break
        if entry_idx is None or entry_idx >= len(df):
            return None
        entry_px = float(df.iloc[entry_idx]["Open"])
    else:  # V3 pure breakout at trigger
        for i in range(2, len(df)):
            if highs[i] >= ohigh:
                entry_idx, entry_px = i, ohigh
                break
        if entry_idx is None:
            return None

    sl = entry_px * (1 - SL_PCT / 100)
    t1 = entry_px * (1 + T1_PCT / 100)
    t2 = entry_px * (1 + T2_PCT / 100)
    pnl, half, reason, px = 0.0, False, None, None
    for i in range(entry_idx, len(df)):
        cl = float(df.iloc[i]["Close"])
        if not half:
            if cl <= sl:
                reason, px, pnl = "SL", sl, (sl-entry_px)/entry_px*100
                break
            if cl >= t2:
                reason, px, pnl = "T2", t2, (t2-entry_px)/entry_px*100
                break
            if cl >= t1:
                half, pnl, sl = True, 0.5*(t1-entry_px)/entry_px*100, entry_px
                continue
        else:
            if cl <= sl:
                reason, px = "T1_BE", sl
                break
            if cl >= t2:
                reason, px, pnl = "T2", t2, pnl + 0.5*(t2-entry_px)/entry_px*100
                break
    if reason is None:
        reason, px = "TIME_EXIT", float(df.iloc[-1]["Close"])
        pnl += (0.5 if half else 1.0) * (px-entry_px)/entry_px*100
    return {"reason": reason, "net": round(pnl - COST_PCT, 3)}


print("=" * 64)
print("PART 1: MANUAL VERIFY stored honest trades vs fresh raw candles")
print("=" * 64)
res = json.load(open("data/backtest_honest_results.json"))
checks = [("ATUL", "2026-07-24"), ("ACUTAAS", "2026-07-27"), ("GODREJCP", "2026-08-13")]
stored = {(t["ticker"], t["trade_date"]): t for t in res["trades"]}
for tk, td in checks:
    st = stored.get((tk, td))
    df = get_15m(tk, td)
    if st is None or df is None or len(df) == 0:
        print(f"{tk} {td}: MISSING"); continue
    # find entry candle by matching stored entry price to an Open
    ent_i = None
    for i in range(len(df)):
        if abs(float(df.iloc[i]["Open"]) - st["entry"]) < 0.01:
            ent_i = i; break
    ok_msg = f"entry open found @ candle {ent_i}" if ent_i is not None else "ENTRY OPEN NOT FOUND!"
    # walk forward manually
    entry = st["entry"]
    sl, t1, t2 = entry*(1-SL_PCT/100), entry*(1+T1_PCT/100), entry*(1+T2_PCT/100)
    pnl, half, reason, px = 0.0, False, "TIME_EXIT", float(df.iloc[-1]["Close"])
    if ent_i is not None:
        for i in range(ent_i, len(df)):
            cl = float(df.iloc[i]["Close"])
            if not half:
                if cl <= sl: reason, px, pnl = "SL", sl, (sl-entry)/entry*100; break
                if cl >= t2: reason, px, pnl = "T2", t2, (t2-entry)/entry*100; break
                if cl >= t1: half, pnl, sl = True, 0.5*(t1-entry)/entry*100, entry; continue
            else:
                if cl <= sl: reason, px = "T1_BE", sl; break
                if cl >= t2: reason, px, pnl = "T2", t2, pnl+0.5*(t2-entry)/entry*100; break
        if reason == "TIME_EXIT":
            pnl += (0.5 if half else 1.0)*(px-entry)/entry*100
    match = (reason == st["exit_reason"] and abs(round(pnl-COST_PCT,3) - st["net_pct"]) < 0.02)
    print(f"{tk} {td}: stored {st['exit_reason']} {st['net_pct']:+.2f}% | "
          f"recomputed {reason} {round(pnl-COST_PCT,3):+.2f}% | {ok_msg} | "
          f"{'MATCH' if match else 'MISMATCH!'}")

print()
print("=" * 64)
print("PART 2: ENTRY-RULE VARIANTS (all honest, no lookahead)")
print("=" * 64)
sys.path.insert(0, ".")
from backtest_honest import build_signal, download, get_nifty500_tickers
from datetime import datetime

tickers = get_nifty500_tickers()
daily, intra = download(tickers)
all_dates = sorted({str(ts.date()) for df in daily.values() for ts in df.index})
today_str = datetime.now(IST).strftime("%Y-%m-%d")
date_idx = {t: {str(ts.date()): i for i, ts in enumerate(df.index)} for t, df in daily.items()}

results = {}
for mode in ("V1", "V2", "V3"):
    trades = []
    for i in range(len(all_dates)-1):
        d, nxt = all_dates[i], all_dates[i+1]
        if d < "2026-07-21" or nxt >= today_str:
            continue
        taken = 0
        for ticker in sorted(date_idx.keys()):
            if taken >= 8:
                break
            idx = date_idx[ticker].get(d)
            if idx is None or idx < 20:
                continue
            sig = build_signal(ticker, daily[ticker], idx)
            if not sig:
                continue
            tr = simulate(intra.get(ticker, {}).get(nxt), mode)
            if tr:
                trades.append(tr); taken += 1
    n = len(trades)
    w = [t for t in trades if t["net"] > 0]
    tot = sum(t["net"] for t in trades)
    results[mode] = {"n": n, "wr": round(len(w)/n*100,1) if n else 0,
                     "sum_pct": round(tot,1)}
    print(f"{mode}: trades={n:3d}  WR={results[mode]['wr']:5.1f}%  "
          f"sum-of-%={results[mode]['sum_pct']:+7.1f}%")

print()
print("=" * 64)
print("PART 3: FAKE-FILL PROOF - ACUTAAS 2026-07-27 (old backtest trade)")
print("=" * 64)
df = get_15m("ACUTAAS", "2026-07-27")
old_entry, trigger = 3233.42, 3422.0
print(f"Old backtest claimed: BUY @ {old_entry} (prev_close*0.992)")
print(f"But entry CONDITION required: High >= {trigger} (opening high)")
print(f"\nActual 15m candles:")
for i, (ts, row) in enumerate(df.iterrows()):
    tag = ""
    if float(row["Low"]) <= old_entry: tag += " <-- low touched old entry"
    if float(row["High"]) >= trigger: tag += " <-- trigger fires HERE"
    print(f"  {ts.strftime('%H:%M')} O={float(row['Open']):7.1f} H={float(row['High']):7.1f} "
          f"L={float(row['Low']):7.1f} C={float(row['Close']):7.1f}{tag}")
    if i >= 7:
        print("  ...")
        break
low_before_trigger = df[df["High"] >= trigger]
first_trig = low_before_trigger.index[0]
lows_after = df.loc[first_trig:, "Low"].min()
print(f"\nLowest LOW after trigger fired: {lows_after}")
print(f"Old entry price {old_entry} reachable after trigger? "
      f"{'YES' if lows_after <= old_entry else 'NO - IMPOSSIBLE FILL'}")
