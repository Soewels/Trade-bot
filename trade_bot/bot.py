"""Live paper-trading loop: haalt periodiek koersen op en simuleert orders.

Er wordt NOOIT met echt geld gehandeld — alle orders gaan door het
paper-trading portfolio. Zie de README voor hoe je dit zou uitbreiden
naar echte orders (op eigen risico).
"""

import logging
import time

from .config import BotConfig
from .data import fetch_candles
from .portfolio import Portfolio
from .strategy import Signal, build_strategy

logger = logging.getLogger("trade_bot")


class TradeBot:
    def __init__(self, config: BotConfig):
        config.validate()
        self.config = config
        self.strategy = build_strategy(config)
        self.portfolio = Portfolio(cash=config.start_cash, fee_rate=config.fee_rate)
        self._running = False

    def step(self) -> None:
        """Eén iteratie: data ophalen, signaal bepalen, eventueel (paper) handelen."""
        cfg = self.config
        candles = fetch_candles(cfg.symbol, cfg.interval, limit=max(cfg.slow_period, cfg.rsi_period) + 50)
        closes = [c.close for c in candles]
        price = closes[-1]

        # Risicobeheer gaat vóór het strategie-signaal
        reason = self.portfolio.check_risk(price, cfg.stop_loss, cfg.take_profit)
        if reason:
            trade = self.portfolio.sell(price, reason=reason)
            if trade:
                logger.info("%s: verkocht %.6f @ %.2f (%s)", cfg.symbol, trade.quantity, trade.price, reason)
                self._log_status(price)
            return

        signal = self.strategy.signal(closes)
        if signal is Signal.BUY and not self.portfolio.in_position:
            trade = self.portfolio.buy(price, cfg.position_size)
            if trade:
                logger.info("%s: gekocht %.6f @ %.2f", cfg.symbol, trade.quantity, trade.price)
        elif signal is Signal.SELL and self.portfolio.in_position:
            trade = self.portfolio.sell(price)
            if trade:
                logger.info("%s: verkocht %.6f @ %.2f", cfg.symbol, trade.quantity, trade.price)
        else:
            logger.debug("%s: signaal=%s prijs=%.2f", cfg.symbol, signal.value, price)
        self._log_status(price)

    def run(self) -> None:
        """Blijf draaien tot Ctrl+C. Fouten (bv. netwerk) worden gelogd en overgeslagen."""
        cfg = self.config
        logger.info("Paper-trading bot gestart: %s %s, strategie=%s, kapitaal=%.2f",
                    cfg.symbol, cfg.interval, cfg.strategy, cfg.start_cash)
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
        finally:
            self._running = False

    def stop(self) -> None:
        self._running = False

    def _log_status(self, price: float) -> None:
        p = self.portfolio
        logger.info("status: cash=%.2f positie=%.6f equity=%.2f (%+.2f%%)",
                    p.cash, p.position, p.equity(price),
                    (p.equity(price) - self.config.start_cash) / self.config.start_cash * 100)
