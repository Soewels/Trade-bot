"""Interactive Brokers broker (EU market mode): UCITS ETFs/ETCs in EUR.

Talks to a running TWS or IB Gateway via the ib_async library (the
maintained fork of ib_insync; both are supported). Use an IBKR paper
account first: log the gateway in as paper (default port 7497 for TWS,
4002 for IB Gateway) and the bot trades with fake money and real data.

Market data: the bot requests delayed data (type 3) so it works without
paid market-data subscriptions; with a subscription you get real-time
quotes automatically.
"""

import logging
import time
from datetime import datetime, time as dtime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from ..models import Bar
from .base import Broker, BrokerError, Fill

log = logging.getLogger("alpaca_bot.broker.ibkr")

# Regular trading hours per IBKR exchange code (holidays are not tracked:
# on a holiday there is simply no fresh data and orders would not fill).
EXCHANGE_HOURS = {
    "IBIS": (dtime(9, 0), dtime(17, 30), "Europe/Berlin"),      # Xetra
    "IBIS2": (dtime(9, 0), dtime(17, 30), "Europe/Berlin"),
    "AEB": (dtime(9, 0), dtime(17, 30), "Europe/Amsterdam"),    # Euronext Amsterdam
    "SBF": (dtime(9, 0), dtime(17, 30), "Europe/Paris"),        # Euronext Paris
    "LSEETF": (dtime(8, 0), dtime(16, 30), "Europe/London"),    # LSE ETF segment
    "LSE": (dtime(8, 0), dtime(16, 30), "Europe/London"),
    "SMART": (dtime(9, 0), dtime(17, 30), "Europe/Berlin"),
    "US": (dtime(9, 30), dtime(16, 0), "America/New_York"),     # NYSE/Nasdaq RTH
    "SEHK": (dtime(9, 30), dtime(16, 0), "Asia/Hong_Kong"),     # Hongkong
    "TSEJ": (dtime(9, 0), dtime(15, 0), "Asia/Tokyo"),          # Tokio
    "EBS": (dtime(9, 0), dtime(17, 30), "Europe/Zurich"),       # SIX Zürich
}

# primaryExchange (uit de scanner) -> sleutel in EXCHANGE_HOURS. Onbekende
# beurzen worden door de screener overgeslagen: nooit handelen op een beurs
# waarvan we de openingstijden niet kennen.
PRIMARY_TO_HOURS = {
    "NYSE": "US", "NASDAQ": "US", "ARCA": "US", "AMEX": "US", "BATS": "US",
    "ISLAND": "US", "IEX": "US",
    "IBIS": "IBIS", "IBIS2": "IBIS", "AEB": "AEB", "SBF": "SBF",
    "EBS": "EBS", "SEHK": "SEHK", "TSEJ": "TSEJ",
}


def hours_for_primary_exchange(primary_exchange: str):
    """EXCHANGE_HOURS-sleutel voor een primaryExchange, of None = onbekend."""
    return PRIMARY_TO_HOURS.get(primary_exchange)


# De scanner geeft contracten vaak terug ZONDER primaryExchange; de
# scan-locatie zelf vertelt dan in welke regio (en dus beurstijden) het
# aandeel noteert.
LOCATION_TO_HOURS = {
    "STK.US.MAJOR": "US", "STK.US": "US",
    "STK.EU.IBIS": "IBIS", "STK.EU.AEB": "AEB", "STK.EU.SBF": "SBF",
    "STK.HK.SEHK": "SEHK", "STK.JP.TSEJ": "TSEJ",
}

# VS-fondsen (ETF's e.d.) zijn door PRIIPs niet toegestaan voor Europese
# particulieren — losse aandelen (incl. REIT's en ADR's) wél.
BLOCKED_STOCK_TYPES = {"ETF", "ETN", "ETC", "ETP", "FUND", "UNIT"}

# reqHistoricalData wants a duration string; pick one that comfortably
# covers `limit` bars of the given size (~200+ bars for the 200 EMA).
DURATION_BY_MINUTES = {15: "10 D", 60: "60 D", 240: "1 Y", 1440: "3 M"}
BAR_SIZE_BY_MINUTES = {15: "15 mins", 60: "1 hour", 240: "4 hours",
                       1440: "1 day"}


def exchange_is_open(exchange: str, now: Optional[datetime] = None) -> bool:
    """Regular-hours check for an IBKR exchange code (weekends excluded;
    exchange holidays are not tracked)."""
    start, end, tz = EXCHANGE_HOURS.get(exchange, EXCHANGE_HOURS["SMART"])
    now = now.astimezone(ZoneInfo(tz)) if now else datetime.now(ZoneInfo(tz))
    if now.weekday() >= 5:  # Saturday/Sunday
        return False
    return start <= now.time() <= end


class IBKRBroker(Broker):
    name = "ibkr"

    def __init__(self, host: str, port: int, client_id: int,
                 instruments: dict[str, dict], allow_shorts: bool = False):
        """`instruments` maps bot symbol -> {"exchange": ..., "currency": ...}."""
        try:
            import ib_async as ib_lib
        except ImportError:
            try:
                import ib_insync as ib_lib
            except ImportError as exc:  # pragma: no cover
                raise BrokerError(
                    "ib_async is not installed. Run: pip install ib_async"
                ) from exc
        self._lib = ib_lib
        self.ib = ib_lib.IB()
        self.host = host
        self.port = port
        self.client_id = client_id
        self.instruments = instruments
        self.allow_shorts = allow_shorts
        self._contracts: dict[str, object] = {}
        self._base_currency: Optional[str] = None
        self._fx_cache: dict[str, tuple[float, float]] = {}  # ccy -> (rate, ts)

    def connect(self) -> None:
        # herverbinden na een gevallen gateway: eerst netjes opruimen,
        # anders weigert ib_async een tweede connect op hetzelfde object
        if self.ib.isConnected():
            return
        self.disconnect()
        self.ib.connect(self.host, self.port, clientId=self.client_id,
                        timeout=20)
        # Delayed data works without paid subscriptions; real-time quotes
        # are used automatically when a subscription exists.
        self.ib.reqMarketDataType(3)
        log.info("connected to IBKR at %s:%s (client id %s)",
                 self.host, self.port, self.client_id)

    def disconnect(self) -> None:
        try:
            self.ib.disconnect()
        except Exception as exc:
            log.debug("IBKR disconnect: %s", exc)

    def _contract(self, symbol: str):
        if symbol in self._contracts:
            return self._contracts[symbol]
        meta = self.instruments[symbol]
        currency = meta.get("currency", "EUR")
        base_exchange = meta.get("exchange", "SMART")
        primary = meta.get("primaryExchange")
        # Noteringen zitten bij IBKR soms net op een andere beurscode
        # (IBIS vs IBIS2 vs FWB): probeer een zoekvolgorde en gebruik wat werkt.
        attempts: list[tuple[str, str | None]] = [(base_exchange, primary)]
        if base_exchange != "SMART":
            attempts.append(("SMART", None))
        if {base_exchange, primary or ""} & {"IBIS", "IBIS2"}:
            for exchange in ("IBIS2", "IBIS", "FWB"):
                if exchange != base_exchange:
                    attempts.append((exchange, None))
        tried = []
        for exchange, primary_exchange in attempts:
            kwargs = {"primaryExchange": primary_exchange} if primary_exchange else {}
            contract = self._lib.Stock(symbol, exchange, currency, **kwargs)
            try:
                result = self.ib.qualifyContracts(contract)
            except Exception:
                result = []
            # ib_async geeft None-plaatshouders terug voor onbekende
            # contracten — die tellen niet als "gevonden"!
            qualified = [c for c in (result or []) if c is not None]
            if qualified:
                if exchange != base_exchange:
                    log.info("%s gevonden via %s (i.p.v. %s)",
                             symbol, exchange, base_exchange)
                self._contracts[symbol] = qualified[0]
                return qualified[0]
            tried.append(exchange)
        raise BrokerError(
            f"IBKR does not recognise {symbol} (geprobeerd: {', '.join(tried)}): "
            "check the ticker/exchange in config.py")

    def add_instrument(self, symbol: str, meta: dict) -> None:
        """Register a (screened) instrument at runtime."""
        self.instruments.setdefault(symbol, meta)

    # --- account ---------------------------------------------------------

    def _account_value(self, tag: str) -> float:
        # accountSummary() kan verouderde cache-waarden teruggeven nadat de
        # gateway is gevallen — expliciet checken, anders blijft een dode
        # verbinding onopgemerkt
        if not self.ib.isConnected():
            raise BrokerError("geen verbinding met IBKR (gateway herstart?)")
        for row in self.ib.accountSummary():
            if row.tag == tag:
                return float(row.value)
        raise BrokerError(f"IBKR account summary has no {tag}")

    def equity(self) -> float:
        return self._account_value("NetLiquidation")

    def buying_power(self, symbol: str) -> float:
        try:
            return self._account_value("BuyingPower")
        except BrokerError:
            return self._account_value("AvailableFunds")

    # --- market data ------------------------------------------------------

    def fetch_bars(self, symbol: str, timeframe_minutes: int,
                   limit: int) -> list[Bar]:
        if timeframe_minutes not in BAR_SIZE_BY_MINUTES:
            raise BrokerError(f"unsupported timeframe: {timeframe_minutes} minutes")
        raw = self.ib.reqHistoricalData(
            self._contract(symbol),
            endDateTime="",
            durationStr=DURATION_BY_MINUTES[timeframe_minutes],
            barSizeSetting=BAR_SIZE_BY_MINUTES[timeframe_minutes],
            whatToShow="TRADES",
            useRTH=True,
            formatDate=2,
        )
        bars = []
        for item in raw:
            if hasattr(item.date, "timestamp"):
                ts = item.date.timestamp()
            else:  # daily bars come back as plain dates
                ts = datetime(item.date.year, item.date.month, item.date.day,
                              tzinfo=timezone.utc).timestamp()
            bars.append(Bar(ts=ts, open=float(item.open), high=float(item.high),
                            low=float(item.low), close=float(item.close),
                            volume=float(item.volume)))
        return bars[-limit:]

    def latest_price(self, symbol: str) -> Optional[float]:
        try:
            ticker = self.ib.reqMktData(self._contract(symbol), "", True, False)
            self.ib.sleep(2.0)
            price = ticker.marketPrice()
            if price and price == price:  # not NaN
                return float(price)
            bars = self.fetch_bars(symbol, 15, 1)
            return bars[-1].close if bars else None
        except Exception as exc:
            log.warning("no latest price for %s: %s", symbol, exc)
            return None

    # --- venue rules -------------------------------------------------------

    def market_open(self, symbol: str) -> bool:
        meta = self.instruments[symbol]
        return exchange_is_open(meta.get("hours") or meta.get("exchange", "SMART"))

    def supports_short(self, symbol: str) -> bool:
        # Shorting UCITS ETFs needs a margin account and borrowable shares;
        # off by default (cash accounts), enable with IBKR_ALLOW_SHORTS=1.
        return self.allow_shorts

    def allows_fractional(self, symbol: str) -> bool:
        return False

    # --- screener support --------------------------------------------------------

    def scan_most_active(self, min_price: float, min_market_cap_musd: float,
                         rows: int = 20,
                         locations: tuple = ("STK.US.MAJOR",)) -> list[dict]:
        """Most active stocks via the IBKR market scanner, per regio-locatie.

        Geeft per kandidaat symbool, valuta en primaire beurs terug; een
        locatie die de scanner niet kent wordt overgeslagen met een log."""
        tags = [self._lib.TagValue("priceAbove", str(min_price)),
                self._lib.TagValue("marketCapAbove1e6", str(min_market_cap_musd))]
        candidates: list[dict] = []
        seen: set[str] = set()
        for location in locations:
            sub = self._lib.ScannerSubscription(
                instrument="STK", locationCode=location,
                scanCode="MOST_ACTIVE", numberOfRows=rows)
            try:
                results = self.ib.reqScannerData(sub, [], tags)
            except Exception as exc:
                log.warning("scanner-locatie %s overgeslagen: %s", location, exc)
                continue
            for row in results:
                details = row.contractDetails
                contract = details.contract
                if contract.symbol in seen:
                    continue
                seen.add(contract.symbol)
                stock_type = (getattr(details, "stockType", "") or "").upper()
                if stock_type in BLOCKED_STOCK_TYPES:
                    log.info("scanner: %s overgeslagen (VS-fonds/%s — niet "
                             "toegestaan voor EU-beleggers)",
                             contract.symbol, stock_type)
                    continue
                primary = contract.primaryExchange or ""
                hours = (PRIMARY_TO_HOURS.get(primary)
                         or LOCATION_TO_HOURS.get(location))
                candidates.append({"symbol": contract.symbol,
                                   "currency": contract.currency,
                                   "primaryExchange": primary,
                                   "hours": hours})
        return candidates

    # --- currency conversion --------------------------------------------------------

    def base_currency(self) -> str:
        if self._base_currency is None:
            for row in self.ib.accountSummary():
                if row.tag == "NetLiquidation":
                    self._base_currency = row.currency or "EUR"
                    break
            else:
                self._base_currency = "EUR"
        return self._base_currency

    def to_base_rate(self, symbol: str) -> float:
        """Multiplier converting instrument-currency amounts to the account's
        base currency (e.g. USD prices -> EUR for a Dutch account), so that
        ATR position sizing risks exactly 1% of equity in the right currency."""
        currency = self.instruments[symbol].get("currency", "EUR")
        base = self.base_currency()
        if currency == base:
            return 1.0
        cached = self._fx_cache.get(currency)
        if cached and time.time() - cached[1] < 3600:
            return cached[0]
        fx_price = self._fx_price(base + currency)  # e.g. EURUSD = USD per EUR
        if not fx_price or fx_price <= 0:
            raise BrokerError(f"no {base}{currency} FX rate available")
        rate = 1.0 / fx_price
        self._fx_cache[currency] = (rate, time.time())
        return rate

    def _fx_price(self, pair: str) -> Optional[float]:
        contract = self._lib.Forex(pair)
        try:
            ticker = self.ib.reqMktData(contract, "", True, False)
            self.ib.sleep(2.0)
            price = ticker.marketPrice()
            if price and price == price:  # not NaN
                return float(price)
        except Exception as exc:
            log.warning("FX snapshot for %s failed: %s", pair, exc)
        try:  # fallback: last daily midpoint
            raw = self.ib.reqHistoricalData(
                contract, endDateTime="", durationStr="2 D",
                barSizeSetting="1 day", whatToShow="MIDPOINT", useRTH=False,
                formatDate=2)
            return float(raw[-1].close) if raw else None
        except Exception as exc:
            log.warning("FX history for %s failed: %s", pair, exc)
            return None

    # --- orders ---------------------------------------------------------------

    def submit_market_order(self, symbol: str, side: str, qty: float) -> Fill:
        # tif expliciet meegeven: zonder tif "corrigeert" IBKR de order naar
        # DAY en stuurt daar melding 10349 over — ib_async kent die code
        # niet, ziet hem aan voor een fout en bestempelt de order als
        # geannuleerd, waarna de bot hem echt annuleerde. Order na order.
        order = self._lib.MarketOrder(side.upper(), qty, tif="DAY")
        trade = self.ib.placeOrder(self._contract(symbol), order)
        deadline = 60.0
        waited = 0.0
        while not trade.isDone() and waited < deadline:
            self.ib.waitOnUpdate(timeout=2.0)
            waited += 2.0
        if trade.orderStatus.status != "Filled":
            self._try_cancel(trade)
            raise BrokerError(
                f"IBKR order for {symbol} not filled "
                f"(status: {trade.orderStatus.status})")
        return Fill(price=float(trade.orderStatus.avgFillPrice),
                    qty=float(trade.orderStatus.filled))

    def _try_cancel(self, trade) -> None:
        try:
            self.ib.cancelOrder(trade.order)
        except Exception as exc:
            log.warning("could not cancel IBKR order: %s", exc)

    # --- reconciliation ---------------------------------------------------------

    def position_symbols(self) -> Optional[set[str]]:
        try:
            return {pos.contract.symbol for pos in self.ib.positions()
                    if pos.position != 0}
        except Exception as exc:
            log.warning("could not list IBKR positions: %s", exc)
            return None
