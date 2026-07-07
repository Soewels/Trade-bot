"""Technische indicatoren, zonder externe dependencies."""

from typing import Optional


def sma(prices: list[float], period: int) -> list[Optional[float]]:
    """Simple Moving Average. Eerste (period-1) waarden zijn None."""
    if period <= 0:
        raise ValueError("period moet positief zijn")
    out: list[Optional[float]] = [None] * len(prices)
    window_sum = 0.0
    for i, price in enumerate(prices):
        window_sum += price
        if i >= period:
            window_sum -= prices[i - period]
        if i >= period - 1:
            out[i] = window_sum / period
    return out


def ema(prices: list[float], period: int) -> list[Optional[float]]:
    """Exponential Moving Average, geseed met de SMA van de eerste periode."""
    if period <= 0:
        raise ValueError("period moet positief zijn")
    out: list[Optional[float]] = [None] * len(prices)
    if len(prices) < period:
        return out
    alpha = 2.0 / (period + 1)
    prev = sum(prices[:period]) / period
    out[period - 1] = prev
    for i in range(period, len(prices)):
        prev = alpha * prices[i] + (1 - alpha) * prev
        out[i] = prev
    return out


def rsi(prices: list[float], period: int = 14) -> list[Optional[float]]:
    """Relative Strength Index volgens Wilder's smoothing."""
    if period <= 0:
        raise ValueError("period moet positief zijn")
    out: list[Optional[float]] = [None] * len(prices)
    if len(prices) <= period:
        return out

    gains = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        change = prices[i] - prices[i - 1]
        if change >= 0:
            gains += change
        else:
            losses -= change
    avg_gain = gains / period
    avg_loss = losses / period
    out[period] = _rsi_value(avg_gain, avg_loss)

    for i in range(period + 1, len(prices)):
        change = prices[i] - prices[i - 1]
        gain = max(change, 0.0)
        loss = max(-change, 0.0)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        out[i] = _rsi_value(avg_gain, avg_loss)
    return out


def _rsi_value(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)
