"""
Volatility functions - USER'S SIMPLE LOGIC, no lookahead.

All functions use ONLY completed candles of day D.

Primary volatility proxy: 15m candles of day D (yfinance keeps 1m only ~7d).
True 1m functions provided and used for the recent-window validation pass.
"""
import pandas as pd


def close_position(high: float, low: float, close: float) -> float:
    """0 = closed at day low, 1 = closed at day high."""
    if high <= low:
        return 0.5
    return (close - low) / (high - low)


def intraday_range_pct(high: float, low: float, close: float) -> float:
    """(High - Low)/Close * 100 - user's definition."""
    if close <= 0:
        return 0.0
    return (high - low) / close * 100.0


def reversal_count(closes) -> int:
    """Number of direction changes in consecutive closes (up->down->up)."""
    c = list(closes)
    if len(c) < 3:
        return 0
    revs = 0
    prev_dir = 0
    for i in range(1, len(c)):
        d = c[i] - c[i - 1]
        cur = 1 if d > 0 else (-1 if d < 0 else 0)
        if cur == 0:
            continue
        if prev_dir != 0 and cur != prev_dir:
            revs += 1
        prev_dir = cur
    return revs


def reversal_ratio(closes) -> float:
    """Reversals / total candles."""
    n = len(list(closes))
    return reversal_count(closes) / n if n else 0.0


def day_volatility_metrics(df_day_intraday: pd.DataFrame) -> dict:
    """Metrics from ONE day's intraday candles (15m proxy or 1m)."""
    if df_day_intraday is None or len(df_day_intraday) < 5:
        return {"reversals": None, "rev_ratio": None}
    closes = df_day_intraday["Close"].tolist()
    return {
        "reversals": reversal_count(closes),
        "rev_ratio": round(reversal_count(closes) / len(closes), 3),
    }


def is_highly_volatile_1m(df_1m: pd.DataFrame, reversals_min: int = 10,
                          ratio_min: float = 0.35) -> dict:
    """TRUE 1-minute volatility check (only possible for last ~7 days).

    Returns {volatile: bool, reversals: int, ratio: float, basis: '1m'}.
    """
    m = day_volatility_metrics(df_1m)
    if m["reversals"] is None:
        return {"volatile": False, "reversals": None, "ratio": None, "basis": "1m"}
    n = len(df_1m)
    return {
        "volatile": m["reversals"] >= reversals_min and m["rev_ratio"] >= ratio_min,
        "reversals": m["reversals"],
        "ratio": m["rev_ratio"],
        "basis": "1m",
    }


def is_highly_volatile_proxy(df_15m_day: pd.DataFrame, reversals_min: int = 10) -> dict:
    """15m-candle proxy for the same idea (usable for months of history).

    Same reversal-count logic; thresholds calibrated for ~25 candles/day.
    """
    m = day_volatility_metrics(df_15m_day)
    if m["reversals"] is None:
        return {"volatile": False, "reversals": None, "ratio": None, "basis": "15m-proxy"}
    return {
        "volatile": m["reversals"] >= reversals_min,
        "reversals": m["reversals"],
        "ratio": m["rev_ratio"],
        "basis": "15m-proxy",
    }
