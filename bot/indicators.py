"""Indicator math for the Alpaca bot, in pure Python (no numpy/pandas)."""

import math
from typing import Optional

from .models import Bar


def sma_last(values: list[float], period: int) -> Optional[float]:
    """Simple moving average of the last `period` values."""
    if period <= 0 or len(values) < period:
        return None
    return sum(values[-period:]) / period


def stdev_last(values: list[float], period: int) -> Optional[float]:
    """Population standard deviation of the last `period` values."""
    if period <= 0 or len(values) < period:
        return None
    window = values[-period:]
    mean = sum(window) / period
    variance = sum((v - mean) ** 2 for v in window) / period
    return math.sqrt(variance)


def ema_series(values: list[float], period: int) -> list[Optional[float]]:
    """Exponential moving average, seeded with the SMA of the first period."""
    if period <= 0:
        raise ValueError("period must be positive")
    out: list[Optional[float]] = [None] * len(values)
    if len(values) < period:
        return out
    alpha = 2.0 / (period + 1)
    prev = sum(values[:period]) / period
    out[period - 1] = prev
    for i in range(period, len(values)):
        prev = alpha * values[i] + (1 - alpha) * prev
        out[i] = prev
    return out


def atr_series(bars: list[Bar], period: int = 14) -> list[Optional[float]]:
    """Average True Range with Wilder's smoothing."""
    if period <= 0:
        raise ValueError("period must be positive")
    out: list[Optional[float]] = [None] * len(bars)
    if len(bars) < period:
        return out
    true_ranges: list[float] = []
    for i, bar in enumerate(bars):
        if i == 0:
            true_ranges.append(bar.high - bar.low)
        else:
            prev_close = bars[i - 1].close
            true_ranges.append(max(
                bar.high - bar.low,
                abs(bar.high - prev_close),
                abs(bar.low - prev_close),
            ))
    prev = sum(true_ranges[:period]) / period
    out[period - 1] = prev
    for i in range(period, len(bars)):
        prev = (prev * (period - 1) + true_ranges[i]) / period
        out[i] = prev
    return out


def atr_last(bars: list[Bar], period: int = 14) -> Optional[float]:
    values = atr_series(bars, period)
    return values[-1] if values else None
