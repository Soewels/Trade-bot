"""Koersdata: Binance publieke API (geen API-key nodig) en CSV-bestanden."""

import csv
from dataclasses import dataclass
from datetime import datetime, timezone

import requests

BINANCE_API = "https://api.binance.com/api/v3"
VALID_INTERVALS = {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "3d", "1w"}


@dataclass
class Candle:
    open_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


def fetch_candles(symbol: str, interval: str = "1h", limit: int = 500,
                  timeout: int = 10) -> list[Candle]:
    """Haal historische candles op via de publieke Binance API."""
    if interval not in VALID_INTERVALS:
        raise ValueError(f"ongeldig interval: {interval}")
    resp = requests.get(
        f"{BINANCE_API}/klines",
        params={"symbol": symbol.upper(), "interval": interval, "limit": min(limit, 1000)},
        timeout=timeout,
    )
    resp.raise_for_status()
    candles = []
    for row in resp.json():
        candles.append(Candle(
            open_time=datetime.fromtimestamp(row[0] / 1000, tz=timezone.utc),
            open=float(row[1]),
            high=float(row[2]),
            low=float(row[3]),
            close=float(row[4]),
            volume=float(row[5]),
        ))
    return candles


def fetch_price(symbol: str, timeout: int = 10) -> float:
    """Huidige prijs van een handelspaar."""
    resp = requests.get(
        f"{BINANCE_API}/ticker/price",
        params={"symbol": symbol.upper()},
        timeout=timeout,
    )
    resp.raise_for_status()
    return float(resp.json()["price"])


def load_candles_csv(path: str) -> list[Candle]:
    """Laad candles uit een CSV met kolommen: timestamp,open,high,low,close,volume.

    timestamp mag een ISO-datum zijn of een Unix-tijd in seconden/milliseconden.
    """
    candles = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            candles.append(Candle(
                open_time=_parse_timestamp(row["timestamp"]),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row.get("volume", 0) or 0),
            ))
    candles.sort(key=lambda c: c.open_time)
    return candles


def _parse_timestamp(value: str) -> datetime:
    value = value.strip()
    try:
        ts = float(value)
        if ts > 1e12:  # milliseconden
            ts /= 1000
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    except ValueError:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
