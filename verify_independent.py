"""
verify_independent.py - INDEPENDENT recheck of strategy_finder.py results.

FRESH yfinance download (no reuse of old result files).
Same logic: volatile whole day + close at low -> next day high.
Entry = REAL open of D+1 first 15m candle. Close-based exits. 0.30% cost.
Max 8/day, Rs 10K/trade. Window 2026-06-01 .. 2026-08-19.

Tests TWO exit-param sets:
  A) user_specified : SL 1.3 / T1 1.2 / T2 2.1 (both markets)
  B) previous_best  : US SL 1.0/T1 1.5/T2 2.1 | NIFTY SL 1.0/T1 1.5/T2 2.8
     (these are what strategy_finder.py actually ranked #1)

Reversal counting: 15m-proxy (same methodology as original run) is PRIMARY
so results are comparable; 1m counts (last 7d) reported separately because
1m flip-counts are on a totally different scale than 15m counts.
"""
import sys, os, json, time, random
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import pandas as pd
import pytz
import yfinance as yf

from main import _clean_yf
from volatility import close_position, intraday_range_pct, reversal_count

try:
    from config import get_nifty500_list as _nifty_fn
    def nifty_tickers():
        return [t.replace(".NS", "") for t in _nifty_fn()]
except Exception:
    from main import get_nifty500_tickers as _nifty_fn
    def nifty_tickers():
        return [t.replace(".NS", "") for t in _nifty_fn()]

IST = pytz.timezone("Asia/Kolkata")
WIN_START, WIN_END = "2026-06-01", "2026-08-19"
COST_PCT, PER_TRADE, MAX_PER_DAY = 0.30, 10000.0, 8

SIGNAL_PARAMS = {
    "US":    {"range_min": 4.0, "cp_max": 0.15, "rev_min": 10, "spike_min": 2.0},
    "NIFTY": {"range_min": 5.0, "cp_max": 0.25, "rev_min": 15, "spike_min": 1.5},
}
EXIT_SETS = {
    "user_specified": {"sl": 1.3, "t1": 1.2, "t2": 2.1},
    "prev_best_US":   {"sl": 1.0, "t1": 1.5, "t2": 2.1},
    "prev_best_NIFTY":{"sl": 1.0, "t1": 1.5, "t2": 2.8},
}


def download_universe(tickers, label):
    daily, i15, i1 = {}, {}, {}

    def _dl(t, period, interval, sink, gdays):
        try:
            df = _clean_yf(yf.download(t, period=period, interval=interval,
                                       auto_adjust=True, progress=False, threads=False), 30)
            if df is None:
                return
            if gdays:
                sink[t] = {str(d): g for d, g in df.groupby(df.index.date)}
            else:
                sink[t] = df
        except Exception:
            pass

    def run(period, interval, sink, gdays, tag):
        pending = list(daily.keys()) if tag != "daily" else tickers
        for attempt in (1, 2):
            if not pending:
                break
            done = set()
            with ThreadPoolExecutor(max_workers=8) as pool:
                futs = {pool.submit(_dl, t, period, interval, sink, gdays): t for t in pending}
                for fut in as_completed(futs):
                    done.add(futs[fut])
            pending = [t for t in pending if t not in done]
            if attempt == 1 and pending:
                time.sleep(2)
        print(f"  [{label}/{tag}] {len(sink)}/{len(pending)+len(sink)} ok")

    print(f"Downloading {label} ({len(tickers)} tickers)...")
    run("1y", "1d", daily, False, "daily")
    run("60d", "15m", i15, True, "15m")
    run("7d", "1m", i1, True, "1m")
    return daily, i15, i1


def build_candidates(daily, i15, i1, market):
    """Signals matching THIS market's exact filters; 15m-proxy primary."""
    sp = SIGNAL_PARAMS[market]
    today_str = datetime.now(IST).strftime("%Y-%m-%d")
    rows = []
    for ticker, df in daily.items():
        dmap = {str(ts.date()): i for i, ts in enumerate(df.index)}
        dates = sorted(dmap.keys())
        vol_ma = df["Volume"].rolling(20).mean()
        for j, d in enumerate(dates):
            if not (WIN_START <= d <= WIN_END):
                continue
            nxt = dates[j + 1] if j + 1 < len(dates) else None
            if nxt is None or nxt > WIN_END or nxt >= today_str:
                continue
            row = df.iloc[dmap[d]]
            h, l, c = float(row["High"]), float(row["Low"]), float(row["Close"])
            rng = intraday_range_pct(h, l, c)
            cp = close_position(h, l, c)
            vma = float(vol_ma.iloc[dmap[d]]) if pd.notna(vol_ma.iloc[dmap[d]]) else 0.0
            spike = float(row["Volume"]) / vma if vma > 0 else 0.0
            if rng < sp["range_min"] or cp > sp["cp_max"] or spike < sp["spike_min"]:
                continue
            day15 = i15.get(ticker, {}).get(d)
            if day15 is None:
                continue
            rev15 = reversal_count(day15["Close"].tolist())
            if rev15 < sp["rev_min"]:
                continue
            ndf = i15.get(ticker, {}).get(nxt)
            if ndf is None or len(ndf) < 6:
                continue
            day1 = i1.get(ticker, {}).get(d)
            rev1m = reversal_count(day1["Close"].tolist()) if day1 is not None else None
            rows.append({"ticker": ticker, "sig_date": d, "next_date": nxt,
                         "range_pct": round(rng, 2), "close_pos": round(cp, 3),
                         "reversals_15m": rev15,
                         "reversals_1m": rev1m,
                         "rev_source": "1m+15m" if rev1m is not None else "15m_proxy",
                         "spike_x": round(spike, 2),
                         "_ndf": ndf})
    print(f"  [{market}] signals: {len(rows)} "
          f"(~{len(rows)/max(len(set(r['sig_date'] for r in rows)),1):.1f}/day)")
    return rows


def simulate_exits(ndf, sl_pct, t1_pct, t2_pct):
    entry = float(ndf.iloc[0]["Open"])          # REAL traded open
    sl = entry * (1 - sl_pct / 100)
    t1 = entry * (1 + t1_pct / 100)
    t2 = entry * (1 + t2_pct / 100)
    pnl, half = 0.0, False
    for i in range(len(ndf)):
        cl = float(ndf.iloc[i]["Close"])
        if not half:
            if cl <= sl:
                return (sl - entry) / entry * 100, "SL"
            if cl >= t2:
                return (t2 - entry) / entry * 100, "T2"
            if cl >= t1:
                half, pnl = True, 0.5 * (t1 - entry) / entry * 100
                sl = entry
                continue
        else:
            if cl <= sl:
                return pnl, "T1_BE"
            if cl >= t2:
                return pnl + 0.5 * (t2 - entry) / entry * 100, "T2"
    last = float(ndf.iloc[-1]["Close"])
    pnl += (0.5 if half else 1.0) * (last - entry) / entry * 100
    return pnl, "TIME_EXIT"


def stats(trades):
    n = len(trades)
    nets = [t["net_pct"] for t in trades]
    wins = [x for x in nets if x > 0]
    gp, gl = sum(wins), abs(sum(x for x in nets if x <= 0))
    return {"trades": n,
            "win_rate": round(len(wins) / n * 100, 1) if n else None,
            "avg_pnl": round(sum(nets) / n, 3) if n else None,
            "total_rs": round(sum(t["pnl_rs"] for t in trades), 0) if n else 0}


def run_market(market, cands, exit_key):
    ex = EXIT_SETS[exit_key]
    by_day = {}
    for c in cands:
        by_day.setdefault(c["sig_date"], []).append(c)
    picked = []
    for d in sorted(by_day.keys()):
        rows = sorted(by_day[d], key=lambda c: c["ticker"])
        picked.extend(rows[:MAX_PER_DAY])
    trades = []
    for c in picked:
        gross, reason = simulate_exits(c["_ndf"], ex["sl"], ex["t1"], ex["t2"])
        net = round(gross - COST_PCT, 3)
        entry_px = float(c["_ndf"].iloc[0]["Open"])
        print(f"    ENTRY PROOF {market} {c['ticker']} {c['next_date']} "
              f"open={entry_px:.2f} (day_df.iloc[0]['Open']) -> {reason} net {net:+.2f}%")
        trades.append({**{k: c[k] for k in ("ticker", "sig_date", "next_date",
                                            "range_pct", "close_pos",
                                            "reversals_15m", "reversals_1m",
                                            "rev_source", "spike_x")},
                       "entry_open": round(entry_px, 2),
                       "net_pct": net, "exit_reason": reason,
                       "pnl_rs": round(PER_TRADE * net / 100, 2)})
    return trades


def write_proofs(all_cands, path):
    random.seed(7)
    lines = ["INDEPENDENT RAW PROOF - verify_independent.py",
             f"Generated: {datetime.now(IST)}",
             "Tags: SIGNAL DAY CLOSE_LOW = day-D daily bar closed in bottom of range;",
             "      ENTRY OPEN REAL      = actual first-15m-candle Open of D+1.", ""]
    for market in ("US", "NIFTY"):
        cands = all_cands[market]
        pool = sorted(cands, key=lambda c: c["next_date"])[-8:]
        picks = random.sample(pool, min(3, len(pool)))
        lines.append("=" * 70 + f"\nMARKET: {market}\n" + "=" * 70)
        suffix = ".NS" if market == "NIFTY" else ""
        for tr in picks:
            tk, sd, td = tr["ticker"], tr["sig_date"], tr["next_date"]
            try:
                dd = yf.download(f"{tk}{suffix}", start=sd,
                                 end=(pd.Timestamp(sd) + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
                                 interval="1d", auto_adjust=True, progress=False, threads=False)
                if isinstance(dd.columns, pd.MultiIndex):
                    dd.columns = dd.columns.get_level_values(0)
                td_df = yf.download(f"{tk}{suffix}", start=td,
                                    end=(pd.Timestamp(td) + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
                                    interval="15m", auto_adjust=True, progress=False, threads=False)
                if isinstance(td_df.columns, pd.MultiIndex):
                    td_df.columns = td_df.columns.get_level_values(0)
                td_df.index = pd.to_datetime(td_df.index)
                if td_df.index.tz is not None:
                    td_df.index = td_df.index.tz_localize(None)
                row = dd.iloc[0]
                lines.append(f"\n--- {tk} | signal {sd} | trade {td} ---")
                lines.append(f"SIGNAL DAY DAILY: O={float(row['Open']):.2f} H={float(row['High']):.2f} "
                             f"L={float(row['Low']):.2f} C={float(row['Close']):.2f} "
                             f"<== SIGNAL DAY CLOSE_LOW (close_pos={tr['close_pos']}, "
                             f"range={tr['range_pct']}%, rev15={tr['reversals_15m']}, "
                             f"rev1m={tr['reversals_1m']}, spike={tr['spike_x']}x)")
                entry = float(td_df.iloc[0]["Open"])
                for i, (ts, r) in enumerate(td_df.iterrows()):
                    tag = "<== ENTRY OPEN REAL @ {:.2f}".format(entry) if i == 0 else ""
                    lines.append(f"  {ts.strftime('%H:%M')} O={float(r['Open']):>9.2f} "
                                 f"H={float(r['High']):>9.2f} L={float(r['Low']):>9.2f} "
                                 f"C={float(r['Close']):>9.2f} {tag}")
                lines.append(f"  (see independent_verification.json for this trade's result)")
            except Exception as e:
                lines.append(f"  ERROR {tk}: {e}")
        lines.append("")
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")


def main():
    t0 = time.time()
    out = {}

    markets = {}
    nifty = nifty_tickers()
    d, i15, i1 = download_universe([f"{t}.NS" for t in nifty], "NIFTY500")
    markets["NIFTY"] = build_candidates(d, i15, i1, "NIFTY")
    from us_tickers import get_us_tickers
    d, i15, i1 = download_universe(get_us_tickers(), "US")
    markets["US"] = build_candidates(d, i15, i1, "US")

    prev_map = {"US": "prev_best_US", "NIFTY": "prev_best_NIFTY"}
    results = {}
    for market in ("US", "NIFTY"):
        results[market] = {}
        for key in ("user_specified", prev_map[market]):
            print(f"\n[{market}] exit set: {key}")
            trades = run_market(market, markets[market], key)
            st = stats(trades)
            reasons = {}
            for t in trades:
                reasons[t["exit_reason"]] = reasons.get(t["exit_reason"], 0) + 1
            st["exit_reasons"] = reasons
            results[market][key] = {"stats": st, "trades_list": trades}

    # 1m-vs-15m reversal scale note
    rev_note = {}
    for market in ("US", "NIFTY"):
        both = [c for c in markets[market] if c["reversals_1m"] is not None]
        rev_note[market] = {
            "signals_with_1m": len(both),
            "avg_rev_15m": round(sum(c["reversals_15m"] for c in both) / len(both), 1) if both else None,
            "avg_rev_1m": round(sum(c["reversals_1m"] for c in both) / len(both), 1) if both else None,
            "note": "1m flip counts are far larger than 15m counts; original ranking "
                    "used 15m-proxy, so primary sim keeps 15m-proxy for comparability."
        }

    # match check on PREVIOUS-BEST set (what strategy_finder ranked #1)
    us_prev = results["US"][prev_map["US"]]["stats"]
    nf_prev = results["NIFTY"][prev_map["NIFTY"]]["stats"]
    us_ok = us_prev["win_rate"] is not None and abs(us_prev["win_rate"] - 63.0) <= 5
    nf_ok = abs((nf_prev["total_rs"] or 0)) <= 500
    verdict = "PASS" if (us_ok and nf_ok) else "FAIL"

    out = {
        "generated": datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S"),
        "window": [WIN_START, WIN_END],
        "us": {**results["US"][prev_map["US"]]["stats"],
               "trades_list": results["US"][prev_map["US"]]["trades_list"],
               "user_specified_exit_variant": results["US"]["user_specified"]["stats"]},
        "nifty": {**results["NIFTY"][prev_map["NIFTY"]]["stats"],
                  "trades_list": results["NIFTY"][prev_map["NIFTY"]]["trades_list"],
                  "user_specified_exit_variant": results["NIFTY"]["user_specified"]["stats"]},
        "reversal_scale_note": rev_note,
        "match_check": verdict + (f" | US WR {us_prev['win_rate']}% vs prev 63% (+/-5); "
                                  f"NIFTY Rs {nf_prev['total_rs']:+,.0f} vs breakeven (+/-500)")
                       if verdict == "PASS" else
                       (f"FAIL | US WR {us_prev['win_rate']}% vs prev 63% (+/-5); "
                        f"NIFTY Rs {nf_prev['total_rs']:+,.0f} vs breakeven (+/-500)"),
    }
    os.makedirs("data", exist_ok=True)
    with open("data/independent_verification.json", "w") as f:
        json.dump(out, f, indent=2)
    write_proofs(markets, "data/independent_raw_proof.txt")

    print("\n===== FINAL TABLE =====")
    print("Market | Prev WR | New WR | Prev PnL  | New PnL   | Match?")
    u = results["US"][prev_map["US"]]["stats"]
    n = results["NIFTY"][prev_map["NIFTY"]]["stats"]
    uu = results["US"]["user_specified"]["stats"]
    nn = results["NIFTY"]["user_specified"]["stats"]
    print(f"US     | 63.0%   | {u['win_rate']}%  | +Rs 3,165 | Rs {u['total_rs']:+,.0f} | {'YES' if us_ok else 'NO'}")
    print(f"NIFTY  | ~0 (BE) | {n['win_rate']}%  | +Rs 3     | Rs {n['total_rs']:+,.0f} | {'YES' if nf_ok else 'NO'}")
    print(f"-- user-specified exits (SL1.3/T1 1.2/T2 2.1): US WR {uu['win_rate']}% Rs {uu['total_rs']:+,.0f} | "
          f"NIFTY WR {nn['win_rate']}% Rs {nn['total_rs']:+,.0f}")
    print(f"MATCH_CHECK: {verdict}")
    print(f"Time: {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
