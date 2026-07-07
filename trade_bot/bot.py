"""Trading-loop: haalt periodiek koersen op en handelt op signalen.

Standaard draait de bot in paper-modus (gesimuleerd geld). Geef een
BinanceExchange mee om echt te handelen — op het testnet of live.

Met strategie "auto" leert de bot gaandeweg bij: elke relearn_hours uur
draait hij een backtest van alle strategieën over de recentste candles en
schakelt hij over naar degene die op de huidige markt het beste presteert.
"""

import logging
import time
from collections import deque
from dataclasses import replace

from .backtest import run_backtest
from .config import BotConfig
from .data import fetch_candles
from .exchange import BinanceExchange, ExchangeError
from .portfolio import Portfolio
from .strategy import STRATEGY_NAMES, Signal, build_strategy

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
    def __init__(self, config: BotConfig, exchange: BinanceExchange | None = None,
                 notifier=None, state_file: str | None = None):
        config.validate()
        self.config = config
        self.exchange = exchange
        self.notifier = notifier
        self.state_file = state_file
        self.active_strategy = "sma_cross" if config.strategy == "auto" else config.strategy
        self.strategy = build_strategy(replace(config, strategy=self.active_strategy))
        self.portfolio = Portfolio(cash=config.start_cash, fee_rate=config.fee_rate)
        self.quote = quote_asset(config.symbol)
        self.paused = False
        self.last_price: float | None = None
        self.equity_history: deque[float] = deque(maxlen=288)  # ~24u bij 5min-polls
        self._last_relearn = 0.0  # 0 → bij "auto" leert de bot meteen bij de eerste stap
        self._running = False

        if exchange is not None:
            free = exchange.free_balance(self.quote)
            budget = min(config.start_cash, free)
            if budget <= 0:
                raise ExchangeError(f"geen vrij {self.quote}-saldo op de exchange")
            self.portfolio.cash = budget
            logger.info("Exchange-saldo: %.2f %s vrij, handelsbudget: %.2f %s%s",
                        free, self.quote, budget, self.quote,
                        " (TESTNET)" if exchange.testnet else " (LIVE)")

        if state_file:
            from .state import load_state
            load_state(self, state_file)

    @property
    def live(self) -> bool:
        return self.exchange is not None

    @property
    def mode(self) -> str:
        if not self.live:
            return "PAPER"
        return "TESTNET" if self.exchange.testnet else "LIVE"

    def set_active_strategy(self, name: str) -> None:
        if name not in STRATEGY_NAMES:
            logger.warning("onbekende strategie %r genegeerd", name)
            return
        self.active_strategy = name
        self.strategy = build_strategy(replace(self.config, strategy=name))

    # -- meldingen & toestand ---------------------------------------------------

    def _notify(self, text: str) -> None:
        if self.notifier:
            self.notifier.send(f"[{self.mode}] {self.config.symbol}\n{text}")

    def _save_state(self) -> None:
        if self.state_file:
            from .state import save_state
            try:
                save_state(self, self.state_file)
            except OSError as exc:
                logger.warning("toestand opslaan mislukt: %s", exc)

    # -- zelflerende strategiekeuze ----------------------------------------------

    def _maybe_relearn(self) -> None:
        if self.config.strategy != "auto":
            return
        if time.monotonic() - self._last_relearn < self.config.relearn_hours * 3600 \
                and self._last_relearn > 0:
            return
        if self.portfolio.in_position:
            return  # niet midden in een trade van gedachten veranderen
        self.relearn()

    def relearn(self) -> None:
        """Backtest alle strategieën op recente data en kies de beste."""
        self._last_relearn = time.monotonic()
        cfg = self.config
        candles = fetch_candles(cfg.symbol, cfg.interval, limit=500)
        results = {}
        for name in STRATEGY_NAMES:
            try:
                result = run_backtest(candles, replace(cfg, strategy=name))
                results[name] = result.total_return_pct
            except ValueError as exc:
                logger.warning("relearn: %s overgeslagen: %s", name, exc)
        if not results:
            return
        best = max(results, key=results.get)
        scores = ", ".join(f"{n}={r:+.2f}%" for n, r in sorted(results.items(),
                                                               key=lambda x: -x[1]))
        logger.info("relearn over %d candles: %s", len(candles), scores)
        if best != self.active_strategy:
            old = self.active_strategy
            self.set_active_strategy(best)
            logger.info("strategie gewisseld: %s → %s", old, best)
            self._notify(f"🧠 Bijgeleerd: strategie gewisseld van {old} naar {best}\n({scores})")
            self._save_state()

    # -- handelslogica ------------------------------------------------------------

    def step(self) -> None:
        """Eén iteratie: data ophalen, signaal bepalen, eventueel handelen."""
        cfg = self.config
        self._maybe_relearn()
        lookback = max(cfg.slow_period, cfg.rsi_period, cfg.macd_slow + cfg.macd_signal)
        candles = fetch_candles(cfg.symbol, cfg.interval, limit=lookback + 50)
        closes = [c.close for c in candles]
        price = closes[-1]
        self.last_price = price
        self.equity_history.append(self.portfolio.equity(price))

        # Risicobeheer gaat vóór het strategie-signaal
        reason = self.portfolio.check_risk(price, cfg.stop_loss, cfg.take_profit)
        if reason:
            self._execute_sell(price, reason)
            self._log_status(price)
            self._save_state()
            return

        signal = self.strategy.signal(closes)
        if signal is Signal.BUY and not self.portfolio.in_position:
            self._execute_buy(price)
        elif signal is Signal.SELL and self.portfolio.in_position:
            self._execute_sell(price, "signal")
        else:
            logger.debug("%s: signaal=%s prijs=%.2f", cfg.symbol, signal.value, price)
        self._log_status(price)
        self._save_state()

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
            self._notify(f"🟢 Gekocht: {fill.quantity:.6f} @ {fill.price:.2f} "
                         f"({fill.quote_amount:.2f} {self.quote})")
        else:
            trade = self.portfolio.buy(price, cfg.position_size)
            if trade:
                logger.info("%s: (paper) gekocht %.8f @ %.2f", cfg.symbol, trade.quantity, trade.price)
                self._notify(f"🟢 Gekocht (papier): {trade.quantity:.6f} @ {trade.price:.2f}")

    def _execute_sell(self, price: float, reason: str) -> None:
        cfg = self.config
        entry = self.portfolio.entry_price
        if self.exchange:
            fill = self.exchange.market_sell(cfg.symbol, self.portfolio.position)
            self.portfolio.record_sell(fill.price, fill.quantity, fill.quote_amount, reason=reason)
            sold_price, quantity = fill.price, fill.quantity
            logger.info("%s: LIVE verkocht %.8f @ %.2f (%s)",
                        cfg.symbol, quantity, sold_price, reason)
        else:
            trade = self.portfolio.sell(price, reason=reason)
            if not trade:
                return
            sold_price, quantity = trade.price, trade.quantity
            logger.info("%s: (paper) verkocht %.8f @ %.2f (%s)",
                        cfg.symbol, quantity, sold_price, reason)
        result_pct = (sold_price - entry) / entry * 100 if entry else 0.0
        icon = "🔴" if reason == "stop_loss" else "🟠" if reason == "noodstop" else "🔵"
        self._notify(f"{icon} Verkocht: {quantity:.6f} @ {sold_price:.2f} "
                     f"({reason}, {result_pct:+.2f}%)")

    def panic(self) -> None:
        """Noodstop: verkoop een eventuele open positie en pauzeer de bot."""
        self.paused = True
        if self.portfolio.in_position and self.last_price:
            self._execute_sell(self.last_price, "noodstop")
            logger.warning("NOODSTOP: positie verkocht en bot gepauzeerd")
        else:
            logger.warning("NOODSTOP: geen open positie, bot gepauzeerd")
            self._notify("🚨 Noodstop: geen open positie, bot gepauzeerd")
        self._save_state()

    # -- levensloop ----------------------------------------------------------------

    def run(self) -> None:
        """Blijf draaien tot Ctrl+C. Fouten (bv. netwerk) worden gelogd en overgeslagen."""
        cfg = self.config
        logger.info("Bot gestart [%s]: %s %s, strategie=%s, budget=%.2f",
                    self.mode, cfg.symbol, cfg.interval, cfg.strategy, self.portfolio.cash)
        self._notify(f"▶️ Bot gestart — strategie: {cfg.strategy}, "
                     f"budget: {self.portfolio.cash:.2f} {self.quote}")
        self._running = True
        try:
            while self._running:
                if not self.paused:
                    try:
                        self.step()
                    except Exception as exc:
                        logger.exception("stap mislukt, opnieuw proberen bij volgende poll")
                        if self.notifier:
                            self.notifier.send_error(f"⚠️ [{self.mode}] {cfg.symbol}: "
                                                     f"stap mislukt: {exc}")
                time.sleep(cfg.poll_seconds)
        except KeyboardInterrupt:
            logger.info("Gestopt door gebruiker.")
            if self.portfolio.in_position:
                logger.warning("LET OP: er staat nog een open positie van %.8f %s",
                               self.portfolio.position, cfg.symbol)
        finally:
            self._running = False
            self._save_state()

    def stop(self) -> None:
        self._running = False

    def _log_status(self, price: float) -> None:
        p = self.portfolio
        logger.info("status: cash=%.2f positie=%.8f equity=%.2f (%+.2f%%) [%s]",
                    p.cash, p.position, p.equity(price),
                    (p.equity(price) - self.config.start_cash) / self.config.start_cash * 100,
                    self.active_strategy)
