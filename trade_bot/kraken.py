"""Kraken-koppeling: publieke koersdata en echt handelen (spot, market orders).

Vereist voor handelen API-keys via environment variables:
    KRAKEN_API_KEY, KRAKEN_API_SECRET

Let op: Kraken heeft geen testnet voor spot-handel. Oefenen doe je met
paper trading (echte Kraken-koersen, gesimuleerd geld).

Symboolnamen: Kraken noemt Bitcoin XBT, dus bv. XBTUSD, XBTEUR, ETHUSD.
"""

import base64
import hashlib
import hmac
import logging
import time
from datetime import datetime, timezone
from decimal import ROUND_DOWN, Decimal
from urllib.parse import urlencode

import requests

from .data import Candle
from .exchange import ExchangeError, Fill

logger = logging.getLogger("trade_bot")

KRAKEN_BASE = "https://api.kraken.com"

# Kraken OHLC-intervallen zijn in minuten
INTERVAL_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "30m": 30,
                    "1h": 60, "4h": 240, "1d": 1440, "1w": 10080}


# -- publieke data (geen API-key nodig) ----------------------------------------

def _public(path: str, timeout: int = 10, **params) -> dict:
    resp = requests.get(f"{KRAKEN_BASE}{path}", params=params, timeout=timeout)
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("error"):
        raise ExchangeError(f"Kraken {path}: {payload['error']}")
    return payload["result"]


def _result_pair_key(result: dict) -> str:
    """Kraken geeft het paar terug onder zijn canonieke naam (bv. XXBTZUSD)."""
    keys = [k for k in result if k != "last"]
    if not keys:
        raise ExchangeError("Kraken gaf geen data terug voor dit paar")
    return keys[0]


def parse_ohlc(rows: list) -> list[Candle]:
    """Zet Kraken-OHLC-rijen [time, open, high, low, close, vwap, volume, count] om."""
    return [Candle(
        open_time=datetime.fromtimestamp(row[0], tz=timezone.utc),
        open=float(row[1]), high=float(row[2]), low=float(row[3]),
        close=float(row[4]), volume=float(row[6]),
    ) for row in rows]


def fetch_candles(symbol: str, interval: str = "1h", limit: int = 500,
                  timeout: int = 10) -> list[Candle]:
    """Haal historische candles op via de publieke Kraken API (max ~720)."""
    if interval not in INTERVAL_MINUTES:
        raise ValueError(f"Kraken ondersteunt interval {interval} niet; "
                         f"kies uit {', '.join(INTERVAL_MINUTES)}")
    result = _public("/0/public/OHLC", timeout=timeout,
                     pair=symbol.upper(), interval=INTERVAL_MINUTES[interval])
    candles = parse_ohlc(result[_result_pair_key(result)])
    return candles[-limit:]


def fetch_price(symbol: str, timeout: int = 10) -> float:
    """Huidige prijs (laatste trade) van een handelspaar."""
    result = _public("/0/public/Ticker", timeout=timeout, pair=symbol.upper())
    return float(result[_result_pair_key(result)]["c"][0])


# -- handelen (API-keys nodig) ---------------------------------------------------

class KrakenExchange:
    """Zelfde interface als BinanceExchange, zodat de bot niets hoeft te weten."""

    testnet = False  # Kraken heeft geen spot-testnet

    def __init__(self, api_key: str, api_secret: str, timeout: int = 10):
        if not api_key or not api_secret:
            raise ValueError("api_key en api_secret zijn verplicht "
                             "(zet KRAKEN_API_KEY en KRAKEN_API_SECRET)")
        try:
            self._secret = base64.b64decode(api_secret)
        except (ValueError, TypeError) as exc:
            raise ValueError("KRAKEN_API_SECRET is geen geldige base64-string; "
                             "kopieer de 'Private key' exact uit Kraken") from exc
        self.api_key = api_key
        self.timeout = timeout
        self.session = requests.Session()
        self._filters: dict[str, dict] = {}

    # -- ondertekening, zie https://docs.kraken.com/rest/#section/Authentication --

    def sign(self, path: str, postdata: str, nonce: str) -> str:
        message = path.encode() + hashlib.sha256((nonce + postdata).encode()).digest()
        return base64.b64encode(hmac.new(self._secret, message, hashlib.sha512).digest()).decode()

    def _private(self, path: str, **data) -> dict:
        data["nonce"] = str(int(time.time() * 1000))
        postdata = urlencode(data)
        headers = {"API-Key": self.api_key,
                   "API-Sign": self.sign(path, postdata, data["nonce"])}
        resp = self.session.post(f"{KRAKEN_BASE}{path}", data=data, headers=headers,
                                 timeout=self.timeout)
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("error"):
            raise ExchangeError(f"Kraken {path}: {payload['error']}")
        return payload["result"]

    # -- account -------------------------------------------------------------------

    def free_balance(self, asset: str) -> float:
        """Saldo van een asset. Kraken gebruikt oude namen: ZUSD, ZEUR, XXBT, XETH."""
        balances = self._private("/0/private/Balance")
        asset = asset.upper()
        aliases = [asset, f"Z{asset}", f"X{asset}"]
        if asset == "BTC":
            aliases += ["XBT", "XXBT"]
        for name in aliases:
            if name in balances:
                return float(balances[name])
        return 0.0

    # -- symboolregels ----------------------------------------------------------------

    def symbol_filters(self, symbol: str) -> dict:
        symbol = symbol.upper()
        if symbol not in self._filters:
            result = _public("/0/public/AssetPairs", timeout=self.timeout, pair=symbol)
            pair_key = _result_pair_key(result)
            info = result[pair_key]
            step = Decimal(1).scaleb(-int(info.get("lot_decimals", 8)))
            self._filters[symbol] = {
                "step_size": f"{step:f}",
                "min_qty": info.get("ordermin", "0"),
                "min_notional": info.get("costmin", "0"),
                "pair_key": pair_key,
            }
        return self._filters[symbol]

    def round_quantity(self, symbol: str, quantity: float) -> float:
        step = Decimal(self.symbol_filters(symbol)["step_size"])
        if step <= 0:
            return quantity
        rounded = (Decimal(str(quantity)) / step).to_integral_value(ROUND_DOWN) * step
        return float(rounded)

    # -- orders ------------------------------------------------------------------------

    def market_buy(self, symbol: str, quote_amount: float) -> Fill:
        """Market-kooporder voor een bedrag in quote-valuta.

        Kraken-orders zijn in base-hoeveelheid, dus we rekenen om via de
        actuele prijs; kleine afwijking wordt door de echte fill gecorrigeerd.
        """
        price = fetch_price(symbol, timeout=self.timeout)
        volume = self.round_quantity(symbol, quote_amount / price)
        if volume <= 0:
            raise ExchangeError("bedrag is te klein voor de minimale ordergrootte")
        result = self._private("/0/private/AddOrder", pair=symbol.upper(),
                               type="buy", ordertype="market",
                               volume=f"{Decimal(str(volume)):f}")
        return self._fill_from_txid(result["txid"][0], "BUY", symbol)

    def market_sell(self, symbol: str, quantity: float) -> Fill:
        quantity = self.round_quantity(symbol, quantity)
        if quantity <= 0:
            raise ExchangeError("hoeveelheid is 0 na afronden op stapgrootte")
        result = self._private("/0/private/AddOrder", pair=symbol.upper(),
                               type="sell", ordertype="market",
                               volume=f"{Decimal(str(quantity)):f}")
        return self._fill_from_txid(result["txid"][0], "SELL", symbol)

    def _fill_from_txid(self, txid: str, side: str, symbol: str,
                        attempts: int = 6, delay: float = 1.0) -> Fill:
        """Vraag de uitvoering van een order op; market orders vullen vrijwel direct."""
        last = None
        for _ in range(attempts):
            orders = self._private("/0/private/QueryOrders", txid=txid)
            last = orders.get(txid, {})
            executed = float(last.get("vol_exec", 0) or 0)
            if last.get("status") == "closed" and executed > 0:
                cost = float(last.get("cost", 0) or 0)
                return Fill(symbol=symbol.upper(), side=side,
                            price=cost / executed, quantity=executed, quote_amount=cost)
            time.sleep(delay)
        raise ExchangeError(f"order {txid} niet (volledig) uitgevoerd: {last}")
