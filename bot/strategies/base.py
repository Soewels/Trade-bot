"""Common strategy interface."""

from typing import Optional

from ..models import Bar, Signal


class Strategy:
    """A strategy evaluates completed candles for one of its symbols.

    Attributes set by subclasses:
      name: short identifier used in logs and trades.csv
      timeframe_minutes: candle size this strategy runs on
      symbols: instruments it trades
      trail_atr_mult: trailing-stop distance in ATRs, or None for no trail
    """

    name: str = "base"
    timeframe_minutes: int = 60
    symbols: list[str] = []
    trail_atr_mult: Optional[float] = None

    def evaluate(self, symbol: str, bars: list[Bar],
                 position_side: Optional[str]) -> Optional[Signal]:
        """Return a Signal, or None to do nothing.

        `bars` contains only completed candles, oldest first.
        `position_side` is "long", "short" or None (flat).
        """
        raise NotImplementedError
