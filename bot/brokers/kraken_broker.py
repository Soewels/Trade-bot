"""Kraken broker (EU market mode): BTC/EUR spot, 24/7.

Reuses the Kraken module of the existing crypto bot (`trade_bot.kraken`)
for public candle data and real market orders. Without KRAKEN_API_KEY /
KRAKEN_API_SECRET the broker runs in *paper mode*: real Kraken prices,
simulated fills against a virtual EUR balance persisted on disk (Kraken
has no testnet for spot trading).
"""

import json
import logging
import os
from typing import Optional

from trade_bot import kraken

from ..models import Bar
from .base import Broker, BrokerError, Fill

log = logging.getLogger("alpaca_bot.broker.kraken")

INTERVAL_BY_MINUTES = {5: "5m", 15: "15m", 30: "30m", 60: "1h", 240: "4h"}


class KrakenBroker(Broker):
    name = "kraken"

    def __init__(self, pairs: dict[str, str], api_key: str = "",
                 api_secret: str = "", paper_cash: float = 10_000.0,
                 paper_state_file: str = "kraken_paper.json"):
        """`pairs` maps bot symbol -> Kraken pair, e.g. {"BTC/EUR": "XBTEUR"}."""
        self.pairs = dict(pairs)
        self.paper_state_file = paper_state_file
        self.exchange = None
        if api_key and api_secret:
            self.exchange = kraken.KrakenExchange(api_key, api_secret)
            log.info("Kraken: live trading enabled")
        else:
            self.paper = self._load_paper_state(paper_cash)
            log.info("Kraken: no API keys, paper mode with EUR %.2f virtual cash",
                     self.paper["cash"])

    @property
    def is_paper(self) -> bool:
        return self.exchange is None

    def connect(self) -> None:
        """Controleer bij het opstarten of Kraken alle geconfigureerde paren kent
        (vangt typfouten in CRYPTO_SYMBOLS meteen af, met een duidelijke melding)."""
        for symbol, pair in self.pairs.items():
            try:
                kraken.fetch_price(pair)
            except Exception as exc:
                raise BrokerError(
                    f"Kraken kent handelspaar {pair} ({symbol}) niet of is "
                    f"onbereikbaar: {exc}") from exc
        log.info("Kraken-paren gecontroleerd: %s", ", ".join(self.pairs.values()))

    def _pair(self, symbol: str) -> str:
        return self.pairs[symbol]

    def add_pair(self, symbol: str, pair: str) -> None:
        """Registreer een (door de screener gekozen) munt tijdens het draaien."""
        self.pairs.setdefault(symbol, pair)

    # --- account ---------------------------------------------------------

    def equity(self) -> float:
        prices = {sym: self.latest_price(sym) or 0.0 for sym in self.pairs}
        if self.is_paper:
            value = self.paper["cash"]
            for sym in self.pairs:
                value += self.paper["qty"].get(sym, 0.0) * prices[sym]
            return value
        value = self.exchange.free_balance("EUR")
        for sym in self.pairs:
            base = sym.split("/")[0]  # "BTC/EUR" -> "BTC"
            value += self.exchange.free_balance(base) * prices[sym]
        return value

    def buying_power(self, symbol: str) -> float:
        if self.is_paper:
            return self.paper["cash"]
        return self.exchange.free_balance("EUR")

    # --- market data ------------------------------------------------------

    def fetch_bars(self, symbol: str, timeframe_minutes: int,
                   limit: int) -> list[Bar]:
        if timeframe_minutes not in INTERVAL_BY_MINUTES:
            raise BrokerError(f"unsupported timeframe: {timeframe_minutes} minutes")
        candles = kraken.fetch_candles(self._pair(symbol),
                                       INTERVAL_BY_MINUTES[timeframe_minutes],
                                       limit=limit)
        return [Bar(ts=c.open_time.timestamp(), open=c.open, high=c.high,
                    low=c.low, close=c.close, volume=c.volume)
                for c in candles]

    def latest_price(self, symbol: str) -> Optional[float]:
        try:
            return kraken.fetch_price(self._pair(symbol))
        except Exception as exc:
            log.warning("no latest price for %s: %s", symbol, exc)
            return None

    # --- venue rules -------------------------------------------------------

    def market_open(self, symbol: str) -> bool:
        return True  # crypto trades 24/7

    def supports_short(self, symbol: str) -> bool:
        return False  # spot only

    def allows_fractional(self, symbol: str) -> bool:
        return True

    # --- orders ---------------------------------------------------------------

    def submit_market_order(self, symbol: str, side: str, qty: float) -> Fill:
        if self.is_paper:
            return self._paper_fill(symbol, side, qty)
        try:
            if side == "buy":
                price = kraken.fetch_price(self._pair(symbol))
                fill = self.exchange.market_buy(self._pair(symbol), qty * price)
            else:
                fill = self.exchange.market_sell(self._pair(symbol), qty)
        except Exception as exc:
            raise BrokerError(f"Kraken order failed: {exc}") from exc
        return Fill(price=fill.price, qty=fill.quantity)

    def _paper_fill(self, symbol: str, side: str, qty: float) -> Fill:
        price = self.latest_price(symbol)
        if price is None:
            raise BrokerError(f"paper fill impossible: no price for {symbol}")
        cost = qty * price
        held = self.paper["qty"].get(symbol, 0.0)
        if side == "buy":
            if cost > self.paper["cash"] + 1e-9:
                raise BrokerError("paper fill impossible: insufficient virtual cash")
            self.paper["cash"] -= cost
            self.paper["qty"][symbol] = held + qty
        else:
            qty = min(qty, held)
            if qty <= 0:
                raise BrokerError("paper fill impossible: nothing held to sell")
            self.paper["cash"] += qty * price
            self.paper["qty"][symbol] = held - qty
        self._save_paper_state()
        log.info("paper fill: %s %s %.8f @ %.2f", side.upper(), symbol, qty, price)
        return Fill(price=price, qty=qty)

    # --- reconciliation ---------------------------------------------------------

    def position_symbols(self) -> Optional[set[str]]:
        if self.is_paper:
            return {sym for sym, qty in self.paper["qty"].items() if qty > 1e-12}
        try:
            held = set()
            for sym in self.pairs:
                base = sym.split("/")[0]
                if self.exchange.free_balance(base) > 1e-8:
                    held.add(sym)
            return held
        except Exception as exc:
            log.warning("could not read Kraken balances: %s", exc)
            return None

    # --- paper state -------------------------------------------------------------

    def _load_paper_state(self, default_cash: float) -> dict:
        if os.path.exists(self.paper_state_file):
            try:
                with open(self.paper_state_file) as handle:
                    data = json.load(handle)
                return {"cash": float(data["cash"]),
                        "qty": dict(data.get("qty", {}))}
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                log.error("could not read %s: %s", self.paper_state_file, exc)
        return {"cash": default_cash, "qty": {}}

    def _save_paper_state(self) -> None:
        tmp = self.paper_state_file + ".tmp"
        with open(tmp, "w") as handle:
            json.dump(self.paper, handle, indent=2)
        os.replace(tmp, self.paper_state_file)
