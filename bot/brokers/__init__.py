"""Broker implementations and the factory that wires them from config."""

from .base import Broker, BrokerError, Fill


def build_brokers(config) -> dict[str, Broker]:
    """Return a map of bot symbol -> connected-to-be broker instance.

    Broker classes are imported lazily so that e.g. the EU profile does not
    require alpaca-trade-api and the US profile does not require ib_async.
    """
    needed = {meta["broker"] for meta in config.INSTRUMENTS.values()}
    brokers: dict[str, Broker] = {}

    if "alpaca" in needed:
        from .alpaca_broker import AlpacaBroker
        crypto = {sym for sym, meta in config.INSTRUMENTS.items()
                  if meta["broker"] == "alpaca" and meta.get("crypto")}
        brokers["alpaca"] = AlpacaBroker(
            config.ALPACA_API_KEY, config.ALPACA_API_SECRET,
            config.ALPACA_BASE_URL, config.ALPACA_DATA_FEED, crypto)

    if "ibkr" in needed:
        from .ibkr_broker import IBKRBroker
        instruments = {sym: meta for sym, meta in config.INSTRUMENTS.items()
                       if meta["broker"] == "ibkr"}
        brokers["ibkr"] = IBKRBroker(
            config.IBKR_HOST, config.IBKR_PORT, config.IBKR_CLIENT_ID,
            instruments, allow_shorts=config.IBKR_ALLOW_SHORTS)

    if "kraken" in needed:
        from .kraken_broker import KrakenBroker
        pairs = {sym: meta["pair"] for sym, meta in config.INSTRUMENTS.items()
                 if meta["broker"] == "kraken"}
        brokers["kraken"] = KrakenBroker(
            pairs, config.KRAKEN_API_KEY, config.KRAKEN_API_SECRET,
            paper_cash=config.KRAKEN_PAPER_CASH,
            paper_state_file=config.KRAKEN_PAPER_STATE_FILE)

    return {sym: brokers[meta["broker"]]
            for sym, meta in config.INSTRUMENTS.items()}


__all__ = ["Broker", "BrokerError", "Fill", "build_brokers"]
