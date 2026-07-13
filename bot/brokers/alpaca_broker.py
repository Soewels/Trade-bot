"""Alpaca broker (US market mode): stocks/ETFs + crypto via alpaca-trade-api."""

import logging
import time
from typing import Optional

from ..models import Bar
from .base import Broker, BrokerError, Fill

log = logging.getLogger("alpaca_bot.broker.alpaca")


class AlpacaBroker(Broker):
    name = "alpaca"

    def __init__(self, api_key: str, api_secret: str, base_url: str,
                 data_feed: str, crypto_symbols: set[str]):
        try:
            from alpaca_trade_api.rest import REST, TimeFrame, TimeFrameUnit
        except ImportError as exc:  # pragma: no cover
            raise BrokerError(
                "alpaca-trade-api is not installed. Run: pip install alpaca-trade-api"
            ) from exc
        self._timeframe_cls = TimeFrame
        self._timeframe_unit = TimeFrameUnit
        self.api = REST(api_key, api_secret, base_url)
        self.data_feed = data_feed
        self.crypto_symbols = set(crypto_symbols)

    def is_crypto(self, symbol: str) -> bool:
        return symbol in self.crypto_symbols

    # --- account ---------------------------------------------------------

    def equity(self) -> float:
        return float(self.api.get_account().equity)

    def buying_power(self, symbol: str) -> float:
        account = self.api.get_account()
        if self.is_crypto(symbol):
            # Crypto is non-marginable: only settled cash can be used.
            value = getattr(account, "non_marginable_buying_power", None) or account.cash
            return float(value)
        return float(account.buying_power)

    # --- market data ------------------------------------------------------

    def _timeframe(self, minutes: int):
        if minutes % 60 == 0:
            return self._timeframe_cls(minutes // 60, self._timeframe_unit.Hour)
        return self._timeframe_cls(minutes, self._timeframe_unit.Minute)

    def fetch_bars(self, symbol: str, timeframe_minutes: int,
                   limit: int) -> list[Bar]:
        timeframe = self._timeframe(timeframe_minutes)
        if self.is_crypto(symbol):
            raw = self.api.get_crypto_bars(symbol, timeframe, limit=limit)
        else:
            raw = self.api.get_bars(symbol, timeframe, limit=limit,
                                    feed=self.data_feed)
        return [Bar(ts=item.t.timestamp(), open=float(item.o), high=float(item.h),
                    low=float(item.l), close=float(item.c), volume=float(item.v))
                for item in raw]

    def latest_price(self, symbol: str) -> Optional[float]:
        try:
            if self.is_crypto(symbol):
                raw = self.api.get_crypto_bars(symbol, self._timeframe_cls.Minute,
                                               limit=1)
                bars = list(raw)
                return float(bars[-1].c) if bars else None
            return float(self.api.get_latest_trade(symbol).price)
        except Exception as exc:
            log.warning("no latest price for %s: %s", symbol, exc)
            return None

    # --- venue rules -------------------------------------------------------

    def market_open(self, symbol: str) -> bool:
        if self.is_crypto(symbol):
            return True
        try:
            return bool(self.api.get_clock().is_open)
        except Exception as exc:
            log.warning("could not read market clock, treating as closed: %s", exc)
            return False

    def supports_short(self, symbol: str) -> bool:
        return not self.is_crypto(symbol)  # Alpaca has no crypto shorts

    def allows_fractional(self, symbol: str) -> bool:
        return self.is_crypto(symbol)

    # --- orders ---------------------------------------------------------------

    def submit_market_order(self, symbol: str, side: str, qty: float) -> Fill:
        tif = "gtc" if self.is_crypto(symbol) else "day"
        order = self.api.submit_order(symbol=symbol, qty=qty, side=side,
                                      type="market", time_in_force=tif)
        filled = self._wait_for_fill(order.id)
        if filled is None:
            self._try_cancel(order.id)
            raise BrokerError(f"order {order.id} for {symbol} did not fill")
        return Fill(price=float(filled.filled_avg_price),
                    qty=float(filled.filled_qty))

    def _wait_for_fill(self, order_id: str, timeout: float = 60.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            order = self.api.get_order(order_id)
            if order.status == "filled":
                return order
            if order.status in ("canceled", "expired", "rejected"):
                log.error("order %s ended with status %s", order_id, order.status)
                return None
            time.sleep(1.0)
        return None

    def _try_cancel(self, order_id: str) -> None:
        try:
            self.api.cancel_order(order_id)
        except Exception as exc:
            log.warning("could not cancel order %s: %s", order_id, exc)

    # --- reconciliation ---------------------------------------------------------

    def position_symbols(self) -> Optional[set[str]]:
        try:
            live = {p.symbol.replace("/", "") for p in self.api.list_positions()}
        except Exception as exc:
            log.warning("could not list Alpaca positions: %s", exc)
            return None
        # Map crypto back to bot-side notation (BTCUSD -> BTC/USD).
        result = set()
        crypto_by_norm = {c.replace("/", ""): c for c in self.crypto_symbols}
        for sym in live:
            result.add(crypto_by_norm.get(sym, sym))
        return result
