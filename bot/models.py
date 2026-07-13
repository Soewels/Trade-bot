"""Shared data types for the Alpaca bot (no external dependencies)."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Bar:
    """One OHLCV candle. `ts` is the bar start time as epoch seconds (UTC)."""
    ts: float
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class Signal:
    """Desired position change emitted by a strategy.

    action: "long", "short" or "exit". The executor reconciles it against the
    current position (e.g. "long" while short means: close short, open long).
    """
    action: str
    reason: str


@dataclass
class PositionState:
    """Bot-side bookkeeping for one open position (persisted across restarts)."""
    symbol: str
    direction: str              # "long" or "short"
    qty: float
    entry_price: float
    entry_time: str             # ISO timestamp
    strategy: str
    atr: float                  # ATR at entry, refreshed on every candle close
    stop_price: float           # hard stop: 1 ATR from entry == 1% of equity
    trail_atr_mult: Optional[float] = None
    peak_price: float = 0.0     # best price seen since entry (worst for shorts)
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "direction": self.direction,
            "qty": self.qty,
            "entry_price": self.entry_price,
            "entry_time": self.entry_time,
            "strategy": self.strategy,
            "atr": self.atr,
            "stop_price": self.stop_price,
            "trail_atr_mult": self.trail_atr_mult,
            "peak_price": self.peak_price,
            "extra": self.extra,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PositionState":
        return cls(**data)
