"""Trend following on EMA crosses (GLD, USO — 4-hour candles).

Entry: 50-period EMA crosses above the 200-period EMA (golden cross) -> long.
The 50 EMA crossing below the 200 EMA (death cross) -> short; while long that
means the executor first closes the long. Exits also happen via the 3x-ATR
trailing stop managed by the risk manager.
"""

from typing import Optional

from ..indicators import ema_series
from ..models import Bar, Signal
from .base import Strategy


class TrendFollowingStrategy(Strategy):
    name = "trend_following"

    def __init__(self, symbols: list[str], fast_period: int = 50,
                 slow_period: int = 200, timeframe_minutes: int = 240,
                 trail_atr_mult: float = 3.0):
        if fast_period >= slow_period:
            raise ValueError("fast_period must be smaller than slow_period")
        self.symbols = list(symbols)
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.timeframe_minutes = timeframe_minutes
        self.trail_atr_mult = trail_atr_mult

    def evaluate(self, symbol: str, bars: list[Bar],
                 position_side: Optional[str]) -> Optional[Signal]:
        closes = [b.close for b in bars]
        if len(closes) < self.slow_period + 1:
            return None
        fast = ema_series(closes, self.fast_period)
        slow = ema_series(closes, self.slow_period)
        prev_fast, prev_slow = fast[-2], slow[-2]
        cur_fast, cur_slow = fast[-1], slow[-1]
        if None in (prev_fast, prev_slow, cur_fast, cur_slow):
            return None

        if prev_fast <= prev_slow and cur_fast > cur_slow and position_side != "long":
            return Signal("long", (
                f"EMA{self.fast_period} crossed above EMA{self.slow_period} "
                f"({cur_fast:.2f} > {cur_slow:.2f})"))
        if prev_fast >= prev_slow and cur_fast < cur_slow and position_side != "short":
            return Signal("short", (
                f"EMA{self.fast_period} crossed below EMA{self.slow_period} "
                f"({cur_fast:.2f} < {cur_slow:.2f})"))
        return None
