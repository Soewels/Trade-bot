"""Handelsstrategieën: geven per candle een signaal BUY / SELL / HOLD."""

from enum import Enum

from .config import BotConfig
from .indicators import macd, rsi, sma


STRATEGY_NAMES = ("sma_cross", "rsi", "macd")


class Signal(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class Strategy:
    """Basisklasse: bepaal een signaal op basis van sluitkoersen tot nu toe."""

    def signal(self, closes: list[float]) -> Signal:
        raise NotImplementedError


class SmaCrossStrategy(Strategy):
    """Koop wanneer de snelle SMA boven de trage kruist, verkoop bij kruising omlaag."""

    def __init__(self, fast_period: int, slow_period: int):
        if fast_period >= slow_period:
            raise ValueError("fast_period moet kleiner zijn dan slow_period")
        self.fast_period = fast_period
        self.slow_period = slow_period

    def signal(self, closes: list[float]) -> Signal:
        if len(closes) < self.slow_period + 1:
            return Signal.HOLD
        fast = sma(closes, self.fast_period)
        slow = sma(closes, self.slow_period)
        prev_fast, prev_slow = fast[-2], slow[-2]
        cur_fast, cur_slow = fast[-1], slow[-1]
        if None in (prev_fast, prev_slow, cur_fast, cur_slow):
            return Signal.HOLD
        if prev_fast <= prev_slow and cur_fast > cur_slow:
            return Signal.BUY
        if prev_fast >= prev_slow and cur_fast < cur_slow:
            return Signal.SELL
        return Signal.HOLD


class RsiStrategy(Strategy):
    """Koop bij oversold (RSI kruist omhoog door de drempel), verkoop bij overbought."""

    def __init__(self, period: int = 14, oversold: float = 30.0, overbought: float = 70.0):
        if not 0 < oversold < overbought < 100:
            raise ValueError("vereist: 0 < oversold < overbought < 100")
        self.period = period
        self.oversold = oversold
        self.overbought = overbought

    def signal(self, closes: list[float]) -> Signal:
        if len(closes) < self.period + 2:
            return Signal.HOLD
        values = rsi(closes, self.period)
        prev, cur = values[-2], values[-1]
        if prev is None or cur is None:
            return Signal.HOLD
        if prev < self.oversold <= cur:
            return Signal.BUY
        if prev > self.overbought >= cur:
            return Signal.SELL
        return Signal.HOLD


class MacdStrategy(Strategy):
    """Koop wanneer de MACD-lijn omhoog kruist door de signaallijn, verkoop bij kruising omlaag."""

    def __init__(self, fast: int = 12, slow: int = 26, signal_period: int = 9):
        if fast >= slow:
            raise ValueError("fast moet kleiner zijn dan slow")
        self.fast = fast
        self.slow = slow
        self.signal_period = signal_period

    def signal(self, closes: list[float]) -> Signal:
        if len(closes) < self.slow + self.signal_period + 1:
            return Signal.HOLD
        macd_line, signal_line, _ = macd(closes, self.fast, self.slow, self.signal_period)
        prev_m, prev_s = macd_line[-2], signal_line[-2]
        cur_m, cur_s = macd_line[-1], signal_line[-1]
        if None in (prev_m, prev_s, cur_m, cur_s):
            return Signal.HOLD
        if prev_m <= prev_s and cur_m > cur_s:
            return Signal.BUY
        if prev_m >= prev_s and cur_m < cur_s:
            return Signal.SELL
        return Signal.HOLD


def build_strategy(config: BotConfig) -> Strategy:
    if config.strategy == "sma_cross":
        return SmaCrossStrategy(config.fast_period, config.slow_period)
    if config.strategy == "rsi":
        return RsiStrategy(config.rsi_period, config.rsi_oversold, config.rsi_overbought)
    if config.strategy == "macd":
        return MacdStrategy(config.macd_fast, config.macd_slow, config.macd_signal)
    raise ValueError(f"onbekende strategie: {config.strategy}")
