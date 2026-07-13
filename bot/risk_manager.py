"""Risk management: ATR-based position sizing, hard stops, trailing stops
and the SPY/QQQ/BTC correlation filter.

Sizing rule: a 1 ATR move against the position equals RISK_PER_TRADE (1%)
of account equity. The hard stop sits exactly 1 ATR from the entry price,
so every trade risks at most 1% of equity — no exceptions.
"""

import math
from typing import Optional

from .models import PositionState


class RiskManager:
    def __init__(self, risk_per_trade: float = 0.01,
                 max_notional_fraction: float = 0.95):
        if not 0 < risk_per_trade < 1:
            raise ValueError("risk_per_trade must be between 0 and 1")
        self.risk_per_trade = risk_per_trade
        self.max_notional_fraction = max_notional_fraction

    def position_size(self, equity: float, price: float, atr: float,
                      buying_power: float, fractional: bool) -> float:
        """Quantity such that a 1 ATR adverse move loses risk_per_trade of equity.

        Quiet instruments (small ATR) get larger positions, volatile ones
        smaller — dollar risk stays constant. The size is capped so the
        notional never exceeds the available buying power.
        """
        if equity <= 0 or price <= 0 or atr <= 0:
            return 0.0
        qty = (equity * self.risk_per_trade) / atr
        max_qty = (buying_power * self.max_notional_fraction) / price
        qty = min(qty, max_qty)
        if fractional:
            return math.floor(qty * 1e6) / 1e6
        return float(math.floor(qty))

    def hard_stop_price(self, entry_price: float, atr: float, direction: str) -> float:
        """Stop exactly 1 ATR from entry: with ATR-based sizing that is 1% of equity."""
        if direction == "long":
            return entry_price - atr
        return entry_price + atr

    def update_trailing_stop(self, state: PositionState, price: float) -> None:
        """Ratchet the trailing stop; it only ever tightens, never loosens."""
        if state.direction == "long":
            state.peak_price = max(state.peak_price, price)
            if state.trail_atr_mult and state.atr > 0:
                trail = state.peak_price - state.trail_atr_mult * state.atr
                state.stop_price = max(state.stop_price, trail)
        else:
            state.peak_price = min(state.peak_price or price, price)
            if state.trail_atr_mult and state.atr > 0:
                trail = state.peak_price + state.trail_atr_mult * state.atr
                state.stop_price = min(state.stop_price, trail)

    def stop_hit(self, state: PositionState, price: float) -> bool:
        if state.direction == "long":
            return price <= state.stop_price
        return price >= state.stop_price

    def correlation_blocks_crypto_long(
            self, position_sides: dict[str, Optional[str]]) -> bool:
        """If SPY and QQQ are both long, block new BTC/USD longs.

        Prevents stacking a third risk-on position on top of two that
        already move together.
        """
        return (position_sides.get("SPY") == "long"
                and position_sides.get("QQQ") == "long")
