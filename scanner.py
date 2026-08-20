"""
Nifty 500 Volatile Down-Close Scanner
Detects signal: big red candle, close near low, volume spike, RSI<40, below SMA20, below VWAP
"""
import pandas as pd
import numpy as np
from typing import List, Tuple, Dict, Any

def compute_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.rolling(window=period, min_periods=period).mean()
    avg_loss = loss.rolling(window=period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100.0 - (100.0 / (1.0 + rs))

def compute_sma(series: pd.Series, period: int = 20) -> pd.Series:
    return series.rolling(window=period, min_periods=period).mean()

def compute_daily_vwap(df: pd.DataFrame) -> pd.Series:
    typical = (df["High"] + df["Low"] + df["Close"]) / 3.0
    tp_vol = (typical * df["Volume"]).rolling(20, min_periods=1).sum()
    vol_sum = df["Volume"].rolling(20, min_periods=1).sum()
    return tp_vol / vol_sum.replace(0, np.nan)

def compute_volume_ma(df: pd.DataFrame, window: int = 20) -> pd.Series:
    return df["Volume"].rolling(window, min_periods=1).mean()

def is_volatile_down_close(
    df: pd.DataFrame,
    idx: int,
    range_pct_thresh: float = 3.5,
    close_pos_max: float = 0.25,
    vol_mult: float = 1.5,
) -> Tuple[bool, Dict[str, Any]]:
    if idx < 20:
        return False, {}

    row = df.iloc[idx]
    o, h, l, c, v = row["Open"], row["High"], row["Low"], row["Close"], row["Volume"]

    if l <= 0 or h <= l:
        return False, {}

    daily_range_pct = (h - l) / l * 100.0
    close_position = (c - l) / (h - l)

    vol_ma = compute_volume_ma(df).iloc[idx]
    volume_spike = v > vol_mult * vol_ma if vol_ma > 0 else False

    vwap = compute_daily_vwap(df).iloc[idx]
    below_vwap = c < vwap if pd.notna(vwap) else c < o

    rsi_series = compute_rsi(df["Close"])
    rsi_val = rsi_series.iloc[idx]
    rsi_ok = pd.notna(rsi_val) and rsi_val < 40

    sma20 = compute_sma(df["Close"], 20).iloc[idx]
    below_sma = pd.notna(sma20) and c < sma20

    details = {
        "daily_range_pct": round(daily_range_pct, 2),
        "close_position": round(close_position, 3),
        "volume": int(v),
        "vol_ma20": int(vol_ma) if pd.notna(vol_ma) else 0,
        "volume_spike": volume_spike,
        "close": round(c, 2),
        "vwap": round(vwap, 2) if pd.notna(vwap) else None,
        "below_vwap": below_vwap,
        "rsi": round(rsi_val, 1) if pd.notna(rsi_val) else None,
        "rsi_ok": rsi_ok,
        "sma20": round(sma20, 2) if pd.notna(sma20) else None,
        "below_sma": below_sma,
    }

    qualifies = (
        daily_range_pct >= range_pct_thresh
        and close_position <= close_pos_max
        and volume_spike
        and below_vwap
        and rsi_ok
        and below_sma
    )
    return qualifies, details

def scan_ticker(daily_df, ticker, range_pct, close_pos_max, vol_mult):
    """Check latest bar for signal. Returns (signal_dict or None, details)."""
    idx = len(daily_df) - 1
    ok, details = is_volatile_down_close(daily_df, idx, range_pct, close_pos_max, vol_mult)
    if not ok:
        return None, details
    return {
        "ticker": ticker,
        "signal_date": daily_df.index[idx].strftime("%Y-%m-%d"),
        "prev_close": round(daily_df.iloc[idx]["Close"], 2),
        "details": details,
    }, details
