# V4 FULL STRATEGY - FINAL VERDICT: NO EDGE

## Summary

- Stage 1 tested 5,760 combinations. Zero passed the original filter: watchlist >= 10, expectancy > 0 on 3/4 days, drawdown > -5%, and trades >= 10. The top five losers were selected only to preserve the locked 1,500-combination Stage 2 invariant.
- Stage 2 tested 1,500 random combinations with seed 42. The best recorded result had 1 trade, expectancy -0.0838%, and PF below 1. The reported result includes 0.10% entry/exit slippage, 0.05% brokerage, and 0.025% India sell-side STT as specified by the run.
- 30-day unseen validation produced 0 trades using a real 5-minute fallback. Yahoo Finance permits only a short recent window for 1-minute history, so true 30-day 1-minute validation was unavailable. Its verdict was `INCONCLUSIVE DUE TO DATA LIMIT`, but it provides no evidence of an edge.
- The CVD calculation is an OHLC volume-allocation proxy, `BuyVol = Vol * (Close - Low) / (High - Low)`, not real trade-level CVD. It should be treated as a noisy proxy.
- Yahoo Finance 1-minute data for 500 tickers is rate-limited, incomplete, and unsuitable for dependable 30-day 1-minute validation.

## Why It Failed

1. Thesis is weak: downtrend plus a short price drop plus an upward CVD proxy showed no statistical edge in the Nifty500 2026 window.
2. Overfitting risk is high: 5,760 Stage 1 combinations plus 1,500 Stage 2 combinations were evaluated on only seven days, and the selected outcomes were losers.
3. Data infrastructure is inadequate: Yahoo Finance cannot supply the required 30-day 1-minute history reliably.

## Decision

- **FREEZE.** Do not create V5 on the same seven-day dataset.
- Do not retune these parameters.
- A future retry requires a proper vendor such as TrueData, GFDL, or Upstox with at least six months of 1-minute data, or a materially different trading thesis.

## Artifacts

- `data/v4_daily.csv`, `data/v4_1m.csv` (Git LFS)
- `data/v4_full_results.json`, `data/v4_full_best_3.md`, `data/v4_full_proof.txt`
- `data/v4_30d_5m.csv` (Git LFS, approximately 109 MB)
- `data/v4_30d_validation_results.json`, `data/v4_30d_validation_report.md`, `data/v4_30d_proof.txt`