"""
Volatility Expansion & Trend-Ride Scanner
Scans for Bollinger Band breakouts with 9/21 EMA trend alignment and volume expansion.
Logs exact rejection reasons for complete auditability.
"""
import pandas as pd
import numpy as np
from typing import Tuple, Dict, Any, Optional

from config import (
    BOLLINGER_PERIOD, BOLLINGER_STD, FAST_EMA, SLOW_EMA,
    VOLUME_MULT, HARD_STOP_LOSS_PCT
)


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Add Bollinger Bands and EMAs to the dataframe."""
    d = df.copy()
    d["SMA20"] = d["Close"].rolling(BOLLINGER_PERIOD).mean()
    d["STD20"] = d["Close"].rolling(BOLLINGER_PERIOD).std()
    d["Upper_Band"] = d["SMA20"] + BOLLINGER_STD * d["STD20"]
    d["Lower_Band"] = d["SMA20"] - BOLLINGER_STD * d["STD20"]
    d["EMA9"] = d["Close"].ewm(span=FAST_EMA, adjust=False).mean()
    d["EMA21"] = d["Close"].ewm(span=SLOW_EMA, adjust=False).mean()
    d["Vol_MA20"] = d["Volume"].rolling(20).mean()
    return d


def evaluate_ticker(df: pd.DataFrame, ticker: str) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Evaluates latest completed daily bar for breakout signal.
    Returns: (qualifies: bool, reject_reason: str, metrics: dict)
    """
    if df is None or len(df) < 30:
        return False, "INSUFFICIENT_DATA (< 30 bars)", {}

    d = compute_indicators(df).dropna(subset=["Upper_Band", "EMA21", "Vol_MA20"])
    if len(d) == 0:
        return False, "DATA_CLEANING_EMPTY", {}

    row = d.iloc[-1]
    cl = float(row["Close"])
    op = float(row["Open"])
    hi = float(row["High"])
    lo = float(row["Low"])
    vol = float(row["Volume"])

    upper = float(row["Upper_Band"])
    ema9 = float(row["EMA9"])
    ema21 = float(row["EMA21"])
    v_ma = float(row["Vol_MA20"])

    vol_ratio = round(vol / v_ma, 2) if v_ma > 0 else 0.0

    metrics = {
        "ticker": ticker,
        "date": d.index[-1].strftime("%Y-%m-%d"),
        "close": round(cl, 2),
        "upper_band": round(upper, 2),
        "ema9": round(ema9, 2),
        "ema21": round(ema21, 2),
        "vol_ma": round(v_ma, 0),
        "vol_ratio": vol_ratio,
        "ema_bullish": ema9 > ema21,
        "band_breakout": cl > upper,
        "vol_spike": vol_ratio >= VOLUME_MULT,
    }

    # Audit Rejection Reasons
    if not (ema9 > ema21):
        return False, f"EMA_BEARISH (EMA9 {ema9:.2f} <= EMA21 {ema21:.2f})", metrics
    if not (cl > upper):
        return False, f"BELOW_BAND (Close {cl:.2f} <= Upper {upper:.2f})", metrics
    if not (vol_ratio >= VOLUME_MULT):
        return False, f"LOW_VOLUME (VolRatio {vol_ratio:.2f}x < {VOLUME_MULT}x)", metrics

    # Signal qualifies!
    metrics["initial_sl"] = round(cl * (1.0 - HARD_STOP_LOSS_PCT / 100.0), 2)
    return True, "QUALIFIED_BREAKOUT", metrics
