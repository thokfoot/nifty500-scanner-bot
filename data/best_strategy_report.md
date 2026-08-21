# Best Strategy Report

Generated: 2026-08-21 14:03:39.853635+05:30

### NIFTY BEST (by expectancy, min 20 trades)
Params: `{'range_min': 5.0, 'cp_max': 0.25, 'rev_min': 15, 'spike_min': 1.5, 'sl': 1.0, 't1': 1.5, 't2': 2.8}`
Trades 23 | WR 43.5% | AvgW +1.41% AvgL -1.08% | PF 1.0 | Exp +0.001%/trade | PnL Rs +3
Exits: {'T2': 3, 'SL': 9, 'TIME_EXIT': 11}

### US BEST (by expectancy, min 20 trades)
Params: `{'range_min': 4.0, 'cp_max': 0.15, 'rev_min': 10, 'spike_min': 2.0, 'sl': 1.0, 't1': 1.5, 't2': 2.1}`
Trades 73 | WR 63.0% | AvgW +1.44% AvgL -1.27% | PF 1.92 | Exp +0.434%/trade | PnL Rs +3,165
Exits: {'SL': 26, 'T2': 35, 'TIME_EXIT': 9, 'T1_BE': 3}

### TOP-3 OVERALL VERIFICATION
1. `US` {'range_min': 4.0, 'cp_max': 0.15, 'rev_min': 10, 'spike_min': 2.0, 'sl': 1.0, 't1': 1.5, 't2': 2.1} -> WR 63.0%, exp +0.434%, PnL Rs +3,165 | Proof PASS
2. `US` {'range_min': 2.5, 'cp_max': 0.15, 'rev_min': 10, 'spike_min': 2.0, 'sl': 1.0, 't1': 1.5, 't2': 2.1} -> WR 63.5%, exp +0.430%, PnL Rs +3,653 | Proof PASS
3. `US` {'range_min': 4.0, 'cp_max': 0.15, 'rev_min': 10, 'spike_min': 2.0, 'sl': 1.0, 't1': 1.5, 't2': 2.8} -> WR 63.0%, exp +0.423%, PnL Rs +3,086 | Proof PASS

### SAME PARAMS BOTH MARKETS
NIFTY: WR 43.5%, exp +0.001%, PnL Rs +3
US:    WR 32.6%, exp -0.403%, PnL Rs -1,732

### HONEST VERDICT
Best edge found: US {'range_min': 4.0, 'cp_max': 0.15, 'rev_min': 10, 'spike_min': 2.0, 'sl': 1.0, 't1': 1.5, 't2': 2.1} exp +0.434%/trade over 73 trades.
Caveat: single 2.5-month window, 15m-proxy volatility, no slippage beyond fixed cost.
