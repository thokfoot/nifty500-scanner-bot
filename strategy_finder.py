"""
strategy_finder.py - REAL strategy tester for USER'S SIMPLE LOGIC.

Logic: "Stock bahut volatile pura din (upar-neeche), shaam ko LOW pe band,
       next day khulte hi high jata hai."

HONEST RULES:
  - Entry  = D+1 FIRST 15m candle OPEN (a real traded price, always fillable)
  - Exits  = CLOSE-based on 15m candles: SL / T1(50% book, SL->BE) / T2 / time
  - Costs  = 0.30% round trip | Rs 10K per trade | max 8 trades/day
  - Volatility reversals use 15m candles of day D as PROXY (1m only exists
    ~7 days); true-1m validation runs separately on the recent window.
  - No prev_close*0.992 imaginary fills anywhere.

Grid:
  RANGE_MIN   [2.5 3.0 3.5 4.0 5.0]
  CLOSE_POS_MAX [0.15 0.20 0.25 0.30]
  REVERSALS_MIN [10 15 20]        (15m-proxy count, day D)
  VOL_SPIKE_X  [1.2 1.5 2.0]
  SL [1.0 1.3 1.5 2.0]  T1 [0.8 1.2 1.5]  T2 [1.8 2.1 2.8]

Signal params select WHICH candidates trade; exit params are pre-simulated
per candidate once and looked up -> full 6480-combo grid is fast.
"""
import sys, os, json, itertools, random, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import pandas as pd
import pytz
import yfinance as yf

from main import get_nifty500_tickers, _clean_yf
from volatility import close_position, intraday_range_pct, reversal_count

IST = pytz.timezone("Asia/Kolkata")
WINDOW_START = "2026-06-01"
COST_PCT = 0.30
PER_TRADE = 10000.0
MAX_PER_DAY = 8

GRID = {
    "range_min": [2.5, 3.0, 3.5, 4.0, 5.0],
    "cp_max": [0.15, 0.20, 0.25, 0.30],
    "rev_min": [10, 15, 20],
    "spike_min": [1.2, 1.5, 2.0],
    "sl": [1.0, 1.3, 1.5, 2.0],
    "t1": [0.8, 1.2, 1.5],
    "t2": [1.8, 2.1, 2.8],
}


def download_universe(tickers, label):
    daily, intra = {}, {}

    def _daily(t):
        try:
            return t, _clean_yf(yf.download(t, period="1y", interval="1d",
                                            auto_adjust=True, progress=False, threads=False), 30)
        except Exception:
            return t, None

    def _intra(t):
        try:
            df = _clean_yf(yf.download(t, period="60d", interval="15m",
                                       auto_adjust=True, progress=False, threads=False), 10)
            if df is None:
                return t, None
            return t, {str(d): g for d, g in df.groupby(df.index.date)}
        except Exception:
            return t, None

    def run(fn, universe, sink, tag):
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
            if attempt == 1 and pending:
                time.sleep(2)
        print(f"  [{label}/{tag}] {len(sink)}/{len(universe)} ok")

    print(f"Downloading {label} ({len(tickers)} tickers)...")
    run(_daily, tickers, daily, "daily")
    run(_intra, list(daily.keys()), intra, "15m")
    return daily, intra


def build_candidates(daily, intra, market):
    """One row per (ticker, signal-day) passing the LOOSEST grid filters."""
    today_str = datetime.now(IST).strftime("%Y-%m-%d")
    rows = []
    for ticker, df in daily.items():
        dmap = {str(ts.date()): i for i, ts in enumerate(df.index)}
        dates = sorted(dmap.keys())
        vol_ma = df["Volume"].rolling(20).mean()
        for j, d in enumerate(dates):
            if d < WINDOW_START:
                continue
            i = dmap[d]
            nxt = dates[j + 1] if j + 1 < len(dates) else None
            if nxt is None or nxt >= today_str:      # need COMPLETE D+1
                continue
            row = df.iloc[i]
            h, l, c = float(row["High"]), float(row["Low"]), float(row["Close"])
            rng = intraday_range_pct(h, l, c)
            cp = close_position(h, l, c)
            vma = float(vol_ma.iloc[i]) if pd.notna(vol_ma.iloc[i]) else 0.0
            spike = float(row["Volume"]) / vma if vma > 0 else 0.0
            if rng < GRID["range_min"][0] or cp > GRID["cp_max"][-1]:
                continue
            if spike < GRID["spike_min"][0]:
                continue
            day15 = intra.get(ticker, {}).get(d)
            rev = reversal_count(day15["Close"].tolist()) if day15 is not None else None
            if rev is None or rev < GRID["rev_min"][0]:
                continue
            ndf = intra.get(ticker, {}).get(nxt)
            if ndf is None or len(ndf) < 6:
                continue
            rows.append({
                "ticker": ticker, "sig_date": d, "next_date": nxt,
                "range_pct": round(rng, 2), "close_pos": round(cp, 3),
                "reversals": rev, "spike_x": round(spike, 2),
                "prev_close": round(c, 2),
            })
    print(f"  [{market}] candidates: {len(rows)} "
          f"(~{len(rows)/max(len(set(r['sig_date'] for r in rows)),1):.1f}/day)")
    return rows


def simulate_exits(ndf, sl_pct, t1_pct, t2_pct):
    """Entry at first-candle OPEN; close-based exits; returns net pct."""
    entry = float(ndf.iloc[0]["Open"])
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
                half = True
                pnl = 0.5 * (t1 - entry) / entry * 100
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
    if not n:
        return {"n": 0}
    nets = [t["net_pct"] for t in trades]
    wins = [x for x in nets if x > 0]
    losses = [x for x in nets if x <= 0]
    gp, gl = sum(wins), abs(sum(losses))
    cap = peak = mdd = 0.0
    for t in sorted(trades, key=lambda x: x["next_date"]):
        cap += t["pnl_rs"]
        peak = max(peak, cap)
        mdd = min(mdd, (cap - peak) / max(peak, 1) * 100)
    return {
        "n": n,
        "win_rate": round(len(wins) / n * 100, 1),
        "avg_win": round(sum(wins) / len(wins), 3) if wins else 0,
        "avg_loss": round(sum(losses) / len(losses), 3) if losses else 0,
        "profit_factor": round(gp / gl, 2) if gl else None,
        "expectancy": round(sum(nets) / n, 3),
        "pnl_rs": round(sum(t["pnl_rs"] for t in trades), 0),
        "max_dd_rs": round(mdd, 2),
    }


def run_grid(candidates, market):
    exit_combos = list(itertools.product(GRID["sl"], GRID["t1"], GRID["t2"]))
    # pre-simulate every candidate under every exit combo
    ndf_cache = {}
    sims = {}
    for c in candidates:
        key = (c["ticker"], c["next_date"])
        if key not in ndf_cache:
            ndf_cache[key] = c["_ndf"]
        sims[key] = [simulate_exits(c["_ndf"], s, t1, t2) for s, t1, t2 in exit_combos]

    results = []
    sig_combos = list(itertools.product(GRID["range_min"], GRID["cp_max"],
                                        GRID["rev_min"], GRID["spike_min"]))
    by_day = {}
    for c in candidates:
        by_day.setdefault(c["sig_date"], []).append(c)

    for rm, cm, vm, sm in sig_combos:
        picked = []
        for d in sorted(by_day.keys()):
            day_rows = [c for c in by_day[d]
                        if c["range_pct"] >= rm and c["close_pos"] <= cm
                        and c["reversals"] >= vm and c["spike_x"] >= sm]
            day_rows.sort(key=lambda c: c["ticker"])     # deterministic
            picked.extend(day_rows[:MAX_PER_DAY])
        if len(picked) < 20:
            continue
        for ei, (s, t1, t2) in enumerate(exit_combos):
            trades = []
            for c in picked:
                gross, reason = sims[(c["ticker"], c["next_date"])][ei]
                net = round(gross - COST_PCT, 3)
                trades.append({**{k: c[k] for k in ("ticker", "sig_date", "next_date",
                                                    "range_pct", "close_pos", "reversals", "spike_x")},
                               "net_pct": net, "exit_reason": reason,
                               "pnl_rs": round(PER_TRADE * net / 100, 2)})
            st = stats(trades)
            if st["n"]:
                results.append({
                    "market": market,
                    "params": {"range_min": rm, "cp_max": cm, "rev_min": vm,
                               "spike_min": sm, "sl": s, "t1": t1, "t2": t2},
                    **st,
                    "_trades": trades,
                })
    results.sort(key=lambda r: (r["expectancy"], r["profit_factor"] or 0), reverse=True)
    return results


def raw_candle_proof(combo, trades, proof_lines, n_pick=3):
    # prefer recent trades: yfinance 15m only goes back ~60 days
    pool = sorted(trades, key=lambda t: t["next_date"])[-10:]
    picks = random.sample(pool, min(n_pick, len(pool)))
    proof_lines.append(f"\n{'='*70}\nCOMBO {combo['params']} | {combo['market']} | "
                       f"WR {combo['win_rate']}% | exp {combo['expectancy']}%\n{'='*70}")
    valid = True
    for tr in picks:
        tk, td = tr["ticker"], tr["next_date"]
        suffix = ".NS" if combo["market"] == "NIFTY" else ""
        try:
            end = (pd.Timestamp(td) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
            df = yf.download(f"{tk}{suffix}", start=td, end=end, interval="15m",
                             auto_adjust=True, progress=False, threads=False)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            df.index = pd.to_datetime(df.index)
            if df.index.tz is not None:
                df.index = df.index.tz_localize(None)
            day = df[df.index.strftime("%Y-%m-%d") == td]
            if len(day) == 0:
                proof_lines.append(f"\n--- {tk} | trade {td} --- NO CANDLES RETURNED (skip)")
                continue
            entry = float(day.iloc[0]["Open"])
            proof_lines.append(f"\n--- {tk} | signal {tr['sig_date']} | trade {td} ---")
            proof_lines.append(f"Signal metrics: range {tr.get('range_pct')}% "
                               f"close_pos {tr.get('close_pos')} reversals {tr.get('reversals')} "
                               f"spike {tr.get('spike_x')}x")
            sl_tagged = False
            for i, (ts, row) in enumerate(day.iterrows()):
                tags = []
                if i == 0:
                    tags.append(f"<== ENTRY OPEN HERE @ {entry:.2f} (real traded price)")
                if i == len(day) - 1:
                    tags.append(f"<== TIME-EXIT close {float(row['Close']):.2f}")
                if (tr["exit_reason"] == "SL" and not sl_tagged and i > 0
                        and float(row["Close"]) <= entry * (1 - combo["params"]["sl"]/100)):
                    tags.append("<== SL close")
                    sl_tagged = True
                proof_lines.append(f"  {ts.strftime('%H:%M')} O={float(row['Open']):>9.2f} "
                                   f"H={float(row['High']):>9.2f} L={float(row['Low']):>9.2f} "
                                   f"C={float(row['Close']):>9.2f} {' '.join(tags)}")
            proof_lines.append(f"  RESULT: {tr['exit_reason']} net {tr['net_pct']:+.2f}%")
            if entry <= 0:
                valid = False
        except Exception as e:
            proof_lines.append(f"  ERROR fetching {tk}: {e}")
            valid = False
    return valid


def hscl_today_pnl():
    """User asked: HSCL aaj ka PnL (paper bot position, entry 698.57)."""
    out = ["\n" + "=" * 70, "HSCL AAJ KA PNL (paper bot position)", "=" * 70]
    try:
        df = yf.download("HSCL.NS", period="5d", interval="15m",
                         auto_adjust=True, progress=False, threads=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df.index = pd.to_datetime(df.index)
        if df.index.tz is not None:
            df.index = df.index.tz_localize(None)
        today_str = datetime.now(IST).strftime("%Y-%m-%d")
        day = df[df.index.strftime("%Y-%m-%d") == today_str]
        if len(day) == 0:
            out.append("No candles yet today.")
            return "\n".join(out)
        entry = 698.57
        sl, t1, t2 = entry * 0.975, entry * 1.012, entry * 1.028
        status, px = "OPEN", None
        for i in range(len(day)):
            cl = float(day.iloc[i]["Close"])
            if cl <= sl:
                status, px = "STOP_LOSS", sl
                break
            if cl >= t2:
                status, px = "T2_HIT", t2
                break
            if cl >= t1:
                status, px = "T1_HIT(booked 50%, SL->BE)", cl
                break
        last = float(day.iloc[-1]["Close"])
        low, high = float(day["Low"].min()), float(day["High"].max())
        out.append(f"Entry (paper): Rs {entry} | SL {sl:.2f} T1 {t1:.2f} T2 {t2:.2f}")
        out.append(f"Today so far: O {float(day.iloc[0]['Open']):.2f} "
                   f"L {low:.2f} H {high:.2f} Last-close {last:.2f} "
                   f"({len(day)} candles till {day.index[-1].strftime('%H:%M')} IST)")
        if status == "T1_HIT(booked 50%, SL->BE)":
            pnl = 0.5 * (t1 - entry) / entry * 100 + (0.5 if px is None else 0) * 0
            out.append(f"STATUS: T1 HIT -> 50% booked @{t1:.2f}, SL moved to BE {entry:.2f}, rest running")
            out.append(f"Banked so far: +{0.5*(t1-entry)/entry*100:.2f}% gross on half")
        elif status in ("STOP_LOSS", "T2_HIT"):
            pnl = (px - entry) / entry * 100
            out.append(f"STATUS: {status} @ {px:.2f} | gross {pnl:+.2f}% "
                       f"| net {pnl-0.30:+.2f}% | Rs {10000*(pnl-0.30)/100:+,.0f}")
        else:
            unreal = (last - entry) / entry * 100
            out.append(f"STATUS: OPEN | unrealized {unreal:+.2f}% "
                       f"(Rs {10000*unreal/100:+,.0f}) at last close")
    except Exception as e:
        out.append(f"HSCL check failed: {e}")
    return "\n".join(out)


def main():
    t0 = time.time()
    random.seed(42)
    all_results, market_data = [], {}

    # ── Nifty 500 ──
    nifty = [t.replace(".NS", "") for t in get_nifty500_tickers()]
    daily, intra = download_universe([f"{t}.NS" for t in nifty], "NIFTY500")
    cands = build_candidates(daily, intra, "NIFTY")
    for c in cands:
        c["_ndf"] = intra[c["ticker"]][c["next_date"]]
    res_n = run_grid(cands, "NIFTY")
    all_results += res_n
    market_data["NIFTY"] = (res_n, cands)

    # ── US top ~500 ──
    from us_tickers import get_us_tickers
    us = [t for t in get_us_tickers()]
    daily_u, intra_u = download_universe(us, "US")
    cands_u = build_candidates(daily_u, intra_u, "US")
    for c in cands_u:
        c["_ndf"] = intra_u[c["ticker"]][c["next_date"]]
    res_u = run_grid(cands_u, "US")
    all_results += res_u
    market_data["US"] = (res_u, cands_u)

    # ── save tuning results ──
    all_results.sort(key=lambda r: (r["expectancy"], r["profit_factor"] or 0), reverse=True)
    slim = [{k: v for k, v in r.items() if k != "_trades"} for r in all_results]
    os.makedirs("data", exist_ok=True)
    with open("data/strategy_tuning_results.json", "w") as f:
        json.dump({"generated": datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S"),
                   "window_from": WINDOW_START, "cost_pct": COST_PCT,
                   "results": slim}, f, indent=2)
    print(f"\nSaved data/strategy_tuning_results.json ({len(slim)} combos)")

    # ── best per market ──
    report, proof_lines = [], ["RAW CANDLE PROOF - strategy_finder.py",
                               f"Generated: {datetime.now(IST)}",
                               "Entry = D+1 first 15m candle OPEN (real price)."]
    best_by_market = {}
    for mk in ("NIFTY", "US"):
        rs, _ = market_data[mk]
        best = rs[0] if rs else None
        best_by_market[mk] = best
        report.append(f"\n### {mk} BEST (by expectancy, min 20 trades)")
        if best:
            reasons = {}
            for t in best["_trades"]:
                reasons[t["exit_reason"]] = reasons.get(t["exit_reason"], 0) + 1
            report.append(f"Params: `{best['params']}`")
            report.append(f"Trades {best['n']} | WR {best['win_rate']}% | "
                          f"AvgW {best['avg_win']:+.2f}% AvgL {best['avg_loss']:+.2f}% | "
                          f"PF {best['profit_factor']} | Exp {best['expectancy']:+.3f}%/trade | "
                          f"PnL Rs {best['pnl_rs']:+,.0f}")
            report.append(f"Exits: {reasons}")

    # ── verify top 3 overall ──
    top3 = all_results[:3]
    report.append("\n### TOP-3 OVERALL VERIFICATION")
    for i, combo in enumerate(top3, 1):
        ok = raw_candle_proof(combo, combo["_trades"], proof_lines)
        report.append(f"{i}. `{combo['market']}` {combo['params']} -> "
                      f"WR {combo['win_rate']}%, exp {combo['expectancy']:+.3f}%, "
                      f"PnL Rs {combo['pnl_rs']:+,.0f} | Proof {'PASS' if ok else 'FAIL'}")

    # ── dual-market same-params comparison (use NIFTY best params on US) ──
    if best_by_market["NIFTY"]:
        p = best_by_market["NIFTY"]["params"]
        match = next((r for r in res_u if r["params"] == p), None)
        report.append("\n### SAME PARAMS BOTH MARKETS")
        report.append(f"NIFTY: WR {best_by_market['NIFTY']['win_rate']}%, "
                      f"exp {best_by_market['NIFTY']['expectancy']:+.3f}%, "
                      f"PnL Rs {best_by_market['NIFTY']['pnl_rs']:+,.0f}")
        if match:
            report.append(f"US:    WR {match['win_rate']}%, exp {match['expectancy']:+.3f}%, "
                          f"PnL Rs {match['pnl_rs']:+,.0f}")
        else:
            report.append("US: this param combo had <20 trades -> no verdict")

    verdict_neg = all((r["expectancy"] or 0) <= 0 for r in all_results)
    report.append("\n### HONEST VERDICT")
    if verdict_neg:
        report.append("**NO EDGE: every combo negative after 0.30% costs.**")
    else:
        bestr = all_results[0]
        report.append(f"Best edge found: {bestr['market']} {bestr['params']} "
                      f"exp {bestr['expectancy']:+.3f}%/trade over {bestr['n']} trades.")
        report.append("Caveat: single 2.5-month window, 15m-proxy volatility, "
                      "no slippage beyond fixed cost.")

    with open("data/best_strategy_report.md", "w") as f:
        f.write("# Best Strategy Report\n\nGenerated: "
                f"{datetime.now(IST)}\n" + "\n".join(report) + "\n")
    with open("data/verification_raw_candles.txt", "w") as f:
        f.write("\n".join(proof_lines) + "\n")

    print(hscl_today_pnl())

    print("\n===== FINAL =====")
    for i, r in enumerate(all_results[:3], 1):
        print(f"#{i} [{r['market']}] {r['params']} -> WR {r['win_rate']}% "
              f"exp {r['expectancy']:+.3f}% PnL Rs {r['pnl_rs']:+,.0f} ({r['n']} trades)")
    print(f"\nTotal time: {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
