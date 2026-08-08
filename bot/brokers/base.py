"""Broker abstraction: strategies and risk management are broker-agnostic;
everything venue-specific (data, orders, account, market hours) lives behind
this interface.
"""

from dataclasses import dataclass
from typing import Optional

from ..models import Bar


class BrokerError(Exception):
    """An order or account call failed at the broker."""


@dataclass
class Fill:
    price: float
    qty: float


class Broker:
    name: str = "base"

    def connect(self) -> None:
        """Establish the connection; called at startup and after a
        detected connection loss (must therefore be safe to call again)."""

    def disconnect(self) -> None:
        """Tear the connection down so connect() can start clean; a no-op
        for brokers without a persistent connection."""

    # --- account ---------------------------------------------------------

    def equity(self) -> float:
        """Total account value in the account's base currency."""
        raise NotImplementedError

    def buying_power(self, symbol: str) -> float:
        raise NotImplementedError

    # --- market data ------------------------------------------------------

    def fetch_bars(self, symbol: str, timeframe_minutes: int,
                   limit: int) -> list[Bar]:
        """Recent candles, oldest first. May include the in-progress candle;
        the bot filters on bar timestamps."""
        raise NotImplementedError

    def latest_price(self, symbol: str) -> Optional[float]:
        raise NotImplementedError

    def to_base_rate(self, symbol: str) -> float:
        """Multiplier converting instrument-currency amounts to the account's
        base currency; 1.0 when they match (the default)."""
        return 1.0

    # --- venue rules -------------------------------------------------------

    def market_open(self, symbol: str) -> bool:
        raise NotImplementedError

    def supports_short(self, symbol: str) -> bool:
        raise NotImplementedError

    def allows_fractional(self, symbol: str) -> bool:
        raise NotImplementedError

    # --- orders ---------------------------------------------------------------

    def submit_market_order(self, symbol: str, side: str, qty: float) -> Fill:
        """Submit a market order ('buy'/'sell') and block until filled.
        Raises BrokerError when the order does not fill."""
        raise NotImplementedError

    # --- reconciliation ---------------------------------------------------------

    def position_symbols(self) -> Optional[set[str]]:
        """Bot-side symbols of open positions at this broker, or None when
        the broker cannot report them (state checks are then skipped)."""
        return None
