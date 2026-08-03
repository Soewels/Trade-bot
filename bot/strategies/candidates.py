"""Kandidaat-strategieën voor het strategie-lab.

Zelfde interface als de vaste strategieën; het lab backtest ze periodiek
tegen elkaar en schakelt alleen over naar een uitdager die de huidige
kampioen consistent verslaat.
"""

from typing import Optional

from trade_bot.indicators import rsi

from ..indicators import ema_series, sma_last, stdev_last
from ..models import Bar, Signal
from .base import Strategy


class RsiDipStrategy(Strategy):
    """Koop wanneer de RSI vanuit oversold weer omhoog kruist; verkoop
    wanneer de RSI is hersteld. Geen trailing stop: de harde 1-ATR-stop
    en het herstel-doel doen het werk."""

    name = "rsi_dip"
    trail_atr_mult = None

    def __init__(self, symbols: list[str], period: int = 14,
                 buy_below: float = 30.0, exit_above: float = 55.0,
                 timeframe_minutes: int = 60):
        self.symbols = list(symbols)
        self.period = period
        self.buy_below = buy_below
        self.exit_above = exit_above
        self.timeframe_minutes = timeframe_minutes

    def evaluate(self, symbol: str, bars: list[Bar],
                 position_side: Optional[str]) -> Optional[Signal]:
        closes = [b.close for b in bars]
        if len(closes) < self.period + 2:
            return None
        values = rsi(closes, self.period)
        prev, cur = values[-2], values[-1]
        if prev is None or cur is None:
            return None
        if position_side == "long" and cur >= self.exit_above:
            return Signal("exit", f"RSI hersteld naar {cur:.0f}")
        if position_side is None and prev < self.buy_below <= cur:
            return Signal("long", f"RSI kruist omhoog uit oversold ({cur:.0f})")
        return None


class BollingerDipStrategy(Strategy):
    """Koop wanneer de koers onder de onderste Bollinger-band sluit;
    verkoop bij terugkeer naar het gemiddelde."""

    name = "bollinger_dip"
    trail_atr_mult = None

    def __init__(self, symbols: list[str], period: int = 20,
                 num_std: float = 2.0, timeframe_minutes: int = 60):
        self.symbols = list(symbols)
        self.period = period
        self.num_std = num_std
        self.timeframe_minutes = timeframe_minutes

    def evaluate(self, symbol: str, bars: list[Bar],
                 position_side: Optional[str]) -> Optional[Signal]:
        closes = [b.close for b in bars]
        mean = sma_last(closes, self.period)
        stdev = stdev_last(closes, self.period)
        if mean is None or stdev is None or stdev == 0:
            return None
        close = closes[-1]
        if position_side == "long" and close >= mean:
            return Signal("exit", f"terug bij gemiddelde {mean:.4f}")
        if position_side is None and close < mean - self.num_std * stdev:
            return Signal("long", f"onder onderste band ({close:.4f})")
        return None


class EmaCrossStrategy(Strategy):
    """Snelle/trage EMA-kruising: long bij kruising omhoog, exit bij
    kruising omlaag; ruime trailing stop laat trends lopen."""

    name = "ema_cross"

    def __init__(self, symbols: list[str], fast: int = 9, slow: int = 21,
                 timeframe_minutes: int = 60, trail_atr_mult: float = 3.0):
        if fast >= slow:
            raise ValueError("fast moet kleiner zijn dan slow")
        self.symbols = list(symbols)
        self.fast = fast
        self.slow = slow
        self.timeframe_minutes = timeframe_minutes
        self.trail_atr_mult = trail_atr_mult

    def evaluate(self, symbol: str, bars: list[Bar],
                 position_side: Optional[str]) -> Optional[Signal]:
        closes = [b.close for b in bars]
        if len(closes) < self.slow + 1:
            return None
        fast = ema_series(closes, self.fast)
        slow = ema_series(closes, self.slow)
        prev_f, prev_s, cur_f, cur_s = fast[-2], slow[-2], fast[-1], slow[-1]
        if None in (prev_f, prev_s, cur_f, cur_s):
            return None
        if (position_side == "long" and prev_f >= prev_s and cur_f < cur_s):
            return Signal("exit", "EMA-kruising omlaag")
        if (position_side is None and prev_f <= prev_s and cur_f > cur_s):
            return Signal("long", f"EMA{self.fast} kruist boven EMA{self.slow}")
        return None
