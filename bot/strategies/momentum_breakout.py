"""Momentum breakout on Donchian channels (BTC/USD — 1-hour candles).

Entry: close breaks above the previous 20-period high with volume at least
1.5x the 20-period average volume. A close below the 20-period low with the
same volume confirmation signals short / exit-long (Alpaca does not support
crypto shorts, so on BTC/USD the executor turns "short" into an exit).
Exits also happen via the 2x-ATR trailing stop managed by the risk manager.
"""

from typing import Optional

from ..models import Bar, Signal
from .base import Strategy


class MomentumBreakoutStrategy(Strategy):
    name = "momentum_breakout"

    def __init__(self, symbols: list[str], period: int = 20,
                 volume_mult: float = 1.5, timeframe_minutes: int = 60,
                 trail_atr_mult: float = 2.0):
        if period < 1:
            raise ValueError("period must be at least 1")
        self.symbols = list(symbols)
        self.period = period
        self.volume_mult = volume_mult
        self.timeframe_minutes = timeframe_minutes
        self.trail_atr_mult = trail_atr_mult

    def evaluate(self, symbol: str, bars: list[Bar],
                 position_side: Optional[str]) -> Optional[Signal]:
        # The breakout channel is built from the bars *before* the current one.
        if len(bars) < self.period + 1:
            return None
        window = bars[-(self.period + 1):-1]
        current = bars[-1]

        channel_high = max(b.high for b in window)
        channel_low = min(b.low for b in window)
        avg_volume = sum(b.volume for b in window) / len(window)
        volume_ok = avg_volume > 0 and current.volume >= self.volume_mult * avg_volume

        if current.close > channel_high and volume_ok and position_side != "long":
            return Signal("long", (
                f"close {current.close:.2f} broke {self.period}-period high "
                f"{channel_high:.2f} on {current.volume / avg_volume:.1f}x volume"))
        if current.close < channel_low and volume_ok and position_side != "short":
            return Signal("short", (
                f"close {current.close:.2f} broke {self.period}-period low "
                f"{channel_low:.2f} on {current.volume / avg_volume:.1f}x volume"))
        return None
