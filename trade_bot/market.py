"""Keuze van de exchange voor koersdata: Binance of Kraken."""

from dataclasses import dataclass
from typing import Callable

from . import data as binance_data
from . import kraken

EXCHANGES = ("binance", "kraken")


@dataclass(frozen=True)
class Market:
    name: str
    fetch_candles: Callable
    fetch_price: Callable


def get_market(name: str) -> Market:
    if name == "binance":
        return Market("binance", binance_data.fetch_candles, binance_data.fetch_price)
    if name == "kraken":
        return Market("kraken", kraken.fetch_candles, kraken.fetch_price)
    raise ValueError(f"onbekende exchange: {name} (kies uit {', '.join(EXCHANGES)})")
