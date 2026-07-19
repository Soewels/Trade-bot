"""Handelsstrategieën: geven per candle een signaal BUY / SELL / HOLD."""

from enum import Enum

from .config import BotConfig
from .indicators import bollinger, donchian, macd, rsi, sma


STRATEGY_NAMES = ("sma_cross", "rsi", "macd", "bollinger", "breakout")


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


class BollingerStrategy(Strategy):
    """Mean-reversion: koop wanneer de koers vanuit de onderband weer omhoog
    kruist, verkoop wanneer hij vanuit de bovenband weer omlaag kruist."""

    def __init__(self, period: int = 20, num_std: float = 2.0):
        if period <= 1 or num_std <= 0:
            raise ValueError("vereist: period > 1 en num_std > 0")
        self.period = period
        self.num_std = num_std

    def signal(self, closes: list[float]) -> Signal:
        if len(closes) < self.period + 1:
            return Signal.HOLD
        _, upper, lower = bollinger(closes, self.period, self.num_std)
        prev_close, cur_close = closes[-2], closes[-1]
        if None in (upper[-2], lower[-2], upper[-1], lower[-1]):
            return Signal.HOLD
        if prev_close <= lower[-2] and cur_close > lower[-1]:
            return Signal.BUY
        if prev_close >= upper[-2] and cur_close < upper[-1]:
            return Signal.SELL
        return Signal.HOLD


class BreakoutStrategy(Strategy):
    """Trendvolgend: koop bij een uitbraak boven het hoogste punt van de
    afgelopen period candles, verkoop bij een val onder het laagste punt."""

    def __init__(self, period: int = 20):
        if period <= 0:
            raise ValueError("period moet positief zijn")
        self.period = period

    def signal(self, closes: list[float]) -> Signal:
        if len(closes) < self.period + 1:
            return Signal.HOLD
        highest, lowest = donchian(closes, self.period)
        if highest[-1] is None or lowest[-1] is None:
            return Signal.HOLD
        if closes[-1] > highest[-1]:
            return Signal.BUY
        if closes[-1] < lowest[-1]:
            return Signal.SELL
        return Signal.HOLD


def build_strategy(config: BotConfig) -> Strategy:
    if config.strategy == "sma_cross":
        return SmaCrossStrategy(config.fast_period, config.slow_period)
    if config.strategy == "rsi":
        return RsiStrategy(config.rsi_period, config.rsi_oversold, config.rsi_overbought)
    if config.strategy == "macd":
        return MacdStrategy(config.macd_fast, config.macd_slow, config.macd_signal)
    if config.strategy == "bollinger":
        return BollingerStrategy(config.bb_period, config.bb_std)
    if config.strategy == "breakout":
        return BreakoutStrategy(config.breakout_period)
    raise ValueError(f"onbekende strategie: {config.strategy}")
