"""Trading-loop: haalt periodiek koersen op en handelt op signalen.

Standaard draait de bot in paper-modus (gesimuleerd geld). Geef een
BinanceExchange mee om echt te handelen — op het testnet of live.
Bij live handelen geldt max_order als bestedingslimiet per aankoop.
"""

import logging
import time

from .config import BotConfig
from .data import fetch_candles
from .exchange import BinanceExchange, ExchangeError
from .portfolio import Portfolio
from .strategy import Signal, build_strategy

logger = logging.getLogger("trade_bot")

QUOTE_ASSETS = ("USDT", "USDC", "FDUSD", "TUSD", "BUSD", "EUR", "BTC", "ETH", "BNB")


def quote_asset(symbol: str) -> str:
    """Bepaal de quote-valuta van een handelspaar, bv. BTCUSDT → USDT."""
    symbol = symbol.upper()
    for asset in QUOTE_ASSETS:
        if symbol.endswith(asset):
            return asset
    raise ValueError(f"kan quote-valuta niet bepalen voor {symbol}")


class TradeBot:
    def __init__(self, config: BotConfig, exchange: BinanceExchange | None = None):
        config.validate()
        self.config = config
        self.exchange = exchange
        self.strategy = build_strategy(config)
        self.portfolio = Portfolio(cash=config.start_cash, fee_rate=config.fee_rate)
        self._running = False

        if exchange is not None:
            asset = quote_asset(config.symbol)
            free = exchange.free_balance(asset)
            budget = min(config.start_cash, free)
            if budget <= 0:
                raise ExchangeError(f"geen vrij {asset}-saldo op de exchange")
            self.portfolio.cash = budget
            logger.info("Exchange-saldo: %.2f %s vrij, handelsbudget: %.2f %s%s",
                        free, asset, budget, asset,
                        " (TESTNET)" if exchange.testnet else " (LIVE)")

    @property
    def live(self) -> bool:
        return self.exchange is not None

    def step(self) -> None:
        """Eén iteratie: data ophalen, signaal bepalen, eventueel handelen."""
        cfg = self.config
        lookback = max(cfg.slow_period, cfg.rsi_period, cfg.macd_slow + cfg.macd_signal)
        candles = fetch_candles(cfg.symbol, cfg.interval, limit=lookback + 50)
        closes = [c.close for c in candles]
        price = closes[-1]

        # Risicobeheer gaat vóór het strategie-signaal
        reason = self.portfolio.check_risk(price, cfg.stop_loss, cfg.take_profit)
        if reason:
            self._execute_sell(price, reason)
            self._log_status(price)
            return

        signal = self.strategy.signal(closes)
        if signal is Signal.BUY and not self.portfolio.in_position:
            self._execute_buy(price)
        elif signal is Signal.SELL and self.portfolio.in_position:
            self._execute_sell(price, "signal")
        else:
            logger.debug("%s: signaal=%s prijs=%.2f", cfg.symbol, signal.value, price)
        self._log_status(price)

    def _execute_buy(self, price: float) -> None:
        cfg = self.config
        if self.exchange:
            spend = min(self.portfolio.cash * cfg.position_size, cfg.max_order)
            min_notional = float(self.exchange.symbol_filters(cfg.symbol)["min_notional"] or 0)
            if spend < max(min_notional, 5.0):
                logger.warning("koopsignaal genegeerd: budget %.2f is onder het minimum", spend)
                return
            fill = self.exchange.market_buy(cfg.symbol, spend)
            self.portfolio.record_buy(fill.price, fill.quantity, fill.quote_amount)
            logger.info("%s: LIVE gekocht %.8f @ %.2f (%.2f besteed)",
                        cfg.symbol, fill.quantity, fill.price, fill.quote_amount)
        else:
            trade = self.portfolio.buy(price, cfg.position_size)
            if trade:
                logger.info("%s: (paper) gekocht %.8f @ %.2f", cfg.symbol, trade.quantity, trade.price)

    def _execute_sell(self, price: float, reason: str) -> None:
        cfg = self.config
        if self.exchange:
            fill = self.exchange.market_sell(cfg.symbol, self.portfolio.position)
            self.portfolio.record_sell(fill.price, fill.quantity, fill.quote_amount, reason=reason)
            logger.info("%s: LIVE verkocht %.8f @ %.2f (%s)",
                        cfg.symbol, fill.quantity, fill.price, reason)
        else:
            trade = self.portfolio.sell(price, reason=reason)
            if trade:
                logger.info("%s: (paper) verkocht %.8f @ %.2f (%s)",
                            cfg.symbol, trade.quantity, trade.price, reason)

    def run(self) -> None:
        """Blijf draaien tot Ctrl+C. Fouten (bv. netwerk) worden gelogd en overgeslagen."""
        cfg = self.config
        mode = "LIVE" if self.live and not (self.exchange and self.exchange.testnet) \
            else ("TESTNET" if self.live else "PAPER")
        logger.info("Bot gestart [%s]: %s %s, strategie=%s, budget=%.2f",
                    mode, cfg.symbol, cfg.interval, cfg.strategy, self.portfolio.cash)
        self._running = True
        try:
            while self._running:
                try:
                    self.step()
                except Exception:
                    logger.exception("stap mislukt, opnieuw proberen bij volgende poll")
                time.sleep(cfg.poll_seconds)
        except KeyboardInterrupt:
            logger.info("Gestopt door gebruiker.")
            if self.portfolio.in_position:
                logger.warning("LET OP: er staat nog een open positie van %.8f %s",
                               self.portfolio.position, cfg.symbol)
        finally:
            self._running = False

    def stop(self) -> None:
        self._running = False

    def _log_status(self, price: float) -> None:
        p = self.portfolio
        logger.info("status: cash=%.2f positie=%.8f equity=%.2f (%+.2f%%)",
                    p.cash, p.position, p.equity(price),
                    (p.equity(price) - self.config.start_cash) / self.config.start_cash * 100)
