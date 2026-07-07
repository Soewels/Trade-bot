"""Binance exchange-koppeling voor echt handelen (spot, market orders).

Vereist API-keys via environment variables:
    BINANCE_API_KEY, BINANCE_API_SECRET

Gebruik testnet=True om gratis te oefenen op https://testnet.binance.vision
(aparte keys nodig, aan te maken via die site).
"""

import hashlib
import hmac
import time
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from urllib.parse import urlencode

import requests

BINANCE_BASE = "https://api.binance.com"
TESTNET_BASE = "https://testnet.binance.vision"


class ExchangeError(RuntimeError):
    """Fout van de exchange (afgekeurde order, ongeldige key, enz.)."""


@dataclass
class Fill:
    """Resultaat van een uitgevoerde order."""
    symbol: str
    side: str            # "BUY" of "SELL"
    price: float         # gemiddelde uitvoeringsprijs
    quantity: float      # uitgevoerde hoeveelheid base-asset
    quote_amount: float  # besteed/ontvangen bedrag in quote-valuta


class BinanceExchange:
    def __init__(self, api_key: str, api_secret: str, testnet: bool = False,
                 timeout: int = 10):
        if not api_key or not api_secret:
            raise ValueError("api_key en api_secret zijn verplicht "
                             "(zet BINANCE_API_KEY en BINANCE_API_SECRET)")
        self.base = TESTNET_BASE if testnet else BINANCE_BASE
        self.testnet = testnet
        self.timeout = timeout
        self._secret = api_secret.encode()
        self.session = requests.Session()
        self.session.headers["X-MBX-APIKEY"] = api_key
        self._filters: dict[str, dict] = {}

    # -- basis ---------------------------------------------------------------

    def sign(self, query: str) -> str:
        """HMAC-SHA256-handtekening over de querystring, zoals Binance vereist."""
        return hmac.new(self._secret, query.encode(), hashlib.sha256).hexdigest()

    def _signed_request(self, method: str, path: str, **params) -> dict:
        params["timestamp"] = int(time.time() * 1000)
        params["recvWindow"] = 5000
        query = urlencode(params)
        url = f"{self.base}{path}?{query}&signature={self.sign(query)}"
        resp = self.session.request(method, url, timeout=self.timeout)
        if resp.status_code != 200:
            raise ExchangeError(f"{method} {path} mislukt ({resp.status_code}): {resp.text}")
        return resp.json()

    def _public_request(self, path: str, **params) -> dict:
        resp = self.session.get(f"{self.base}{path}", params=params, timeout=self.timeout)
        if resp.status_code != 200:
            raise ExchangeError(f"GET {path} mislukt ({resp.status_code}): {resp.text}")
        return resp.json()

    # -- account -------------------------------------------------------------

    def free_balance(self, asset: str) -> float:
        """Vrij (niet in orders vastzittend) saldo van een asset, bv. 'USDT'."""
        account = self._signed_request("GET", "/api/v3/account")
        for balance in account.get("balances", []):
            if balance["asset"] == asset.upper():
                return float(balance["free"])
        return 0.0

    # -- symboolregels -------------------------------------------------------

    def symbol_filters(self, symbol: str) -> dict:
        """LOT_SIZE-stapgrootte en minimum ordergrootte voor een symbool (gecachet)."""
        symbol = symbol.upper()
        if symbol not in self._filters:
            info = self._public_request("/api/v3/exchangeInfo", symbol=symbol)
            filters = {"step_size": "0.00000001", "min_qty": "0", "min_notional": "0"}
            for f in info["symbols"][0]["filters"]:
                if f["filterType"] == "LOT_SIZE":
                    filters["step_size"] = f["stepSize"]
                    filters["min_qty"] = f["minQty"]
                elif f["filterType"] in ("NOTIONAL", "MIN_NOTIONAL"):
                    filters["min_notional"] = f.get("minNotional", "0")
            self._filters[symbol] = filters
        return self._filters[symbol]

    def round_quantity(self, symbol: str, quantity: float) -> float:
        """Rond een hoeveelheid naar beneden af op de stapgrootte van het symbool."""
        step = Decimal(self.symbol_filters(symbol)["step_size"])
        if step <= 0:
            return quantity
        rounded = (Decimal(str(quantity)) / step).to_integral_value(ROUND_DOWN) * step
        return float(rounded)

    # -- orders --------------------------------------------------------------

    def market_buy(self, symbol: str, quote_amount: float) -> Fill:
        """Plaats een market-kooporder voor een bedrag in quote-valuta (bv. USDT)."""
        result = self._signed_request(
            "POST", "/api/v3/order",
            symbol=symbol.upper(), side="BUY", type="MARKET",
            quoteOrderQty=f"{quote_amount:.2f}",
        )
        return self._fill_from_order(result, "BUY")

    def market_sell(self, symbol: str, quantity: float) -> Fill:
        """Plaats een market-verkooporder; de hoeveelheid wordt op stapgrootte afgerond."""
        quantity = self.round_quantity(symbol, quantity)
        if quantity <= 0:
            raise ExchangeError("hoeveelheid is 0 na afronden op stapgrootte")
        result = self._signed_request(
            "POST", "/api/v3/order",
            symbol=symbol.upper(), side="SELL", type="MARKET",
            quantity=f"{Decimal(str(quantity)):f}",
        )
        return self._fill_from_order(result, "SELL")

    @staticmethod
    def _fill_from_order(order: dict, side: str) -> Fill:
        quantity = float(order.get("executedQty", 0) or 0)
        quote = float(order.get("cummulativeQuoteQty", 0) or 0)
        if quantity <= 0:
            raise ExchangeError(f"order niet (volledig) uitgevoerd: {order}")
        return Fill(
            symbol=order["symbol"],
            side=side,
            price=quote / quantity,
            quantity=quantity,
            quote_amount=quote,
        )
