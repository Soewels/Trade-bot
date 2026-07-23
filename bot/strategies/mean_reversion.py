"""Mean reversion on Bollinger-style z-scores (SPY, QQQ — 15-minute candles).

Entry: close more than N standard deviations away from the 20-period SMA
(N = 1.5 for SPY, 1.8 for QQQ) — long below the band, short above it.
Exit: price returns to the moving average. The hard 1-ATR stop from the
risk manager stays active the whole time; there is no trailing stop.
"""

from typing import Optional

from ..indicators import sma_last, stdev_last
from ..models import Bar, Signal
from .base import Strategy


class MeanReversionStrategy(Strategy):
    name = "mean_reversion"
    trail_atr_mult = None

    def __init__(self, thresholds: dict[str, float], period: int = 20,
                 timeframe_minutes: int = 15):
        if period < 2:
            raise ValueError("period must be at least 2")
        for symbol, k in thresholds.items():
            if k <= 0:
                raise ValueError(f"threshold for {symbol} must be positive")
        self.thresholds = dict(thresholds)
        self.period = period
        self.timeframe_minutes = timeframe_minutes
        self.symbols = list(thresholds)

    def add_symbol(self, symbol: str, threshold: float) -> None:
        """Add an instrument at runtime (used by the US stock screener)."""
        if threshold <= 0:
            raise ValueError("threshold must be positive")
        self.thresholds[symbol] = threshold
        if symbol not in self.symbols:
            self.symbols.append(symbol)

    def remove_symbol(self, symbol: str) -> None:
        self.thresholds.pop(symbol, None)
        if symbol in self.symbols:
            self.symbols.remove(symbol)

    def evaluate(self, symbol: str, bars: list[Bar],
                 position_side: Optional[str]) -> Optional[Signal]:
        closes = [b.close for b in bars]
        mean = sma_last(closes, self.period)
        stdev = stdev_last(closes, self.period)
        if mean is None or stdev is None:
            return None
        close = closes[-1]

        # Exit first: position has reverted back to the mean.
        if position_side == "long" and close >= mean:
            return Signal("exit", f"price {close:.2f} reverted to mean {mean:.2f}")
        if position_side == "short" and close <= mean:
            return Signal("exit", f"price {close:.2f} reverted to mean {mean:.2f}")
        if position_side is not None or stdev == 0:
            return None

        z_score = (close - mean) / stdev
        threshold = self.thresholds[symbol]
        if z_score <= -threshold:
            return Signal("long", f"z-score {z_score:.2f} below -{threshold}")
        if z_score >= threshold:
            return Signal("short", f"z-score {z_score:.2f} above +{threshold}")
        return None
