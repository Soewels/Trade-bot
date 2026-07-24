"""Multi-instrument trading bot — main loop.

Runs three strategies on five instruments, on the market chosen with
BOT_MARKET in .env:

  eu (default): mean reversion on SXR8 & SXRV, breakout on BTC/EUR,
                trend following on 4GLD & OD7F — UCITS ETFs/ETCs in EUR
                via Interactive Brokers, BTC/EUR via Kraken.
  us:           the same strategies on SPY, QQQ, BTC/USD, GLD and USO
                via Alpaca.

The loop wakes every POLL_SECONDS to check hard/trailing stops against the
latest price, and evaluates each strategy whenever one of its candles has
closed. Equities only trade during their exchange's market hours; crypto
runs 24/7.

Run from the project root:  python -m bot.main
"""

import logging
import sys
import time
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from bot import crypto_screener, screener
from bot.brokers import BrokerError, build_brokers
from bot.indicators import atr_last
from bot.models import Bar, Signal
from bot.portfolio import Portfolio
from bot.risk_manager import RiskManager
from bot.strategies import (MeanReversionStrategy, MomentumBreakoutStrategy,
                            TrendFollowingStrategy)
from bot.strategies.base import Strategy
from trade_bot.notify import TelegramNotifier

log = logging.getLogger("alpaca_bot")


class PrefixedNotifier:
    """Zet een label voor elke melding, zodat deze bot en de oude crypto-bot
    dezelfde Telegram-chat kunnen delen zonder verwarring."""

    def __init__(self, inner, prefix: str):
        self.inner = inner
        self.prefix = prefix

    def _tag(self, text: str) -> str:
        return f"{self.prefix} {text}" if self.prefix else text

    def send(self, text: str) -> bool:
        return self.inner.send(self._tag(text))

    def send_error(self, text: str) -> bool:
        return self.inner.send_error(self._tag(text))


def build_strategies() -> list[Strategy]:
    mr = config.MEAN_REVERSION
    mb = config.MOMENTUM_BREAKOUT
    tf = config.TREND_FOLLOWING
    return [
        MeanReversionStrategy(thresholds=mr["symbols"], period=mr["period"],
                              timeframe_minutes=mr["timeframe_minutes"]),
        MomentumBreakoutStrategy(symbols=mb["symbols"], period=mb["period"],
                                 volume_mult=mb["volume_mult"],
                                 timeframe_minutes=mb["timeframe_minutes"],
                                 trail_atr_mult=mb["trail_atr_mult"]),
        TrendFollowingStrategy(symbols=tf["symbols"], fast_period=tf["fast_period"],
                               slow_period=tf["slow_period"],
                               timeframe_minutes=tf["timeframe_minutes"],
                               trail_atr_mult=tf["trail_atr_mult"]),
    ]


class Bot:
    def __init__(self, brokers, notifier=None):
        """`brokers` maps each tradable symbol to a broker instance."""
        self.brokers = brokers
        self.notifier = notifier
        self.strategies = build_strategies()
        self.risk = RiskManager(config.RISK_PER_TRADE, config.MAX_NOTIONAL_FRACTION,
                                risk_on_pair=config.RISK_ON_PAIR)
        self.portfolio = Portfolio(brokers, config.STATE_FILE,
                                   config.TRADES_CSV, config.DAILY_PNL_CSV,
                                   notifier=notifier)
        # last evaluated candle bucket per (strategy, symbol)
        self.last_bucket: dict[tuple[str, str], int] = {}
        # dashboard-besturing
        self.paused = False
        self.close_all_requested = False
        self.status: dict = {"ts": 0, "equity": None, "positions": [],
                             "universe": [], "crypto_universe": [],
                             "brokers": [], "equity_history": [],
                             "paused": False,
                             "market": config.BOT_MARKET, "note": ""}
        # brokerverbindingen: handel met wat wél verbonden is, blijf de rest
        # proberen (zo draait crypto al terwijl de IB Gateway nog ontbreekt)
        self.pending_brokers: set = set(self.brokers.values())
        self.connected_brokers: set = set()
        self._next_connect_try = 0.0
        self._connect_delay = 15.0
        self._restore_us_stocks()
        self._restore_crypto()

    def try_connect_brokers(self) -> None:
        """Probeer (met backoff) de nog niet verbonden brokers te verbinden."""
        if not self.pending_brokers or time.time() < self._next_connect_try:
            return
        for broker in sorted(self.pending_brokers, key=lambda b: b.name):
            try:
                broker.connect()
                self.pending_brokers.discard(broker)
                self.connected_brokers.add(broker)
                self._connect_delay = 15.0
                log.info("verbonden met %s", broker.name)
                self.portfolio.notify(f"🔌 Verbonden met {broker.name} — "
                                      "instrumenten van deze broker doen nu mee")
            except Exception as exc:
                log.warning("verbinding met %s lukt nog niet (%s); "
                            "nieuwe poging over %.0fs",
                            broker.name, exc, self._connect_delay)
        if self.pending_brokers:
            self._next_connect_try = time.time() + self._connect_delay
            self._connect_delay = min(self._connect_delay * 2, 120)

    def total_equity(self) -> float:
        """Vermogen over de brokers die nu verbonden zijn."""
        return sum(broker.equity() for broker in self.connected_brokers)

    # --- US stock screener --------------------------------------------------------

    def _ibkr_broker(self):
        return next((b for b in set(self.brokers.values()) if b.name == "ibkr"),
                    None)

    def _mean_reversion(self):
        return next(s for s in self.strategies if s.name == "mean_reversion")

    def _register_us_stock(self, symbol: str) -> None:
        broker = self._ibkr_broker()
        broker.add_instrument(symbol, dict(screener.US_STOCK_META))
        self.brokers[symbol] = broker
        self._mean_reversion().add_symbol(symbol, config.US_STOCK_THRESHOLD)

    def _restore_us_stocks(self) -> None:
        """Re-register the screened universe from state, before reconcile —
        open positions in these stocks must stay manageable after a restart."""
        if self._ibkr_broker() is None:
            return
        for symbol in self.portfolio.meta.get("us_stocks", []):
            self._register_us_stock(symbol)

    # --- crypto-screener ------------------------------------------------------------

    def _kraken_broker(self):
        return next((b for b in set(self.brokers.values()) if b.name == "kraken"),
                    None)

    def _momentum(self):
        return next(s for s in self.strategies if s.name == "momentum_breakout")

    def _crypto_symbols(self) -> list[str]:
        broker = self._kraken_broker()
        return [s for s in self._momentum().symbols
                if self.brokers.get(s) is broker]

    def _register_crypto(self, symbol: str) -> None:
        broker = self._kraken_broker()
        broker.add_pair(symbol, config.kraken_pair(symbol))
        self.brokers[symbol] = broker
        momentum = self._momentum()
        if symbol not in momentum.symbols:
            momentum.symbols.append(symbol)

    def _restore_crypto(self) -> None:
        if self._kraken_broker() is None:
            return
        for symbol in self.portfolio.meta.get("crypto_universe", []):
            self._register_crypto(symbol)

    def maybe_screen_crypto(self) -> None:
        """Scan alle Kraken-EUR-munten en houd de sterkste stijgers aan."""
        broker = self._kraken_broker()
        if (config.CRYPTO_AUTO_COUNT <= 0 or broker is None
                or broker not in self.connected_brokers):
            return
        meta = self.portfolio.meta
        last = float(meta.get("crypto_screened_ts", 0))
        if time.time() - last < config.CRYPTO_RESCAN_HOURS * 3600:
            return
        meta["crypto_screened_ts"] = time.time()  # ook bij falen: geen retry-storm
        self.portfolio.save()
        from bot.brokers.kraken_broker import INTERVAL_BY_MINUTES
        try:
            picks = crypto_screener.scan_kraken(
                config.CRYPTO_AUTO_COUNT, config.CRYPTO_MIN_EUR_VOLUME,
                interval=INTERVAL_BY_MINUTES[config.CRYPTO_TIMEFRAME_MINUTES])
        except Exception as exc:
            log.warning("crypto-scan mislukt (nieuwe poging over %.0f uur): %s",
                        config.CRYPTO_RESCAN_HOURS, exc)
            return
        momentum = self._momentum()
        current = self._crypto_symbols()
        held = {s for s in current if s in self.portfolio.positions}
        universe = screener.merge_universe(current, held, picks,
                                           config.CRYPTO_AUTO_COUNT)
        for symbol in current:
            if symbol not in universe:
                momentum.symbols.remove(symbol)
                self.brokers.pop(symbol, None)
        for symbol in universe:
            if symbol not in current:
                self._register_crypto(symbol)
        if universe != current:
            self.portfolio.notify("🪙 Crypto-selectie bijgewerkt: "
                                  + (", ".join(universe) or "geen stijgers gevonden"))
        meta["crypto_universe"] = universe
        self.portfolio.save()
        log.info("crypto universe: %s", ", ".join(universe) or "leeg")

    def maybe_screen_us_stocks(self) -> None:
        """(Re)screen for liquid US stocks when the universe is stale."""
        broker = self._ibkr_broker()
        if (config.US_STOCK_COUNT <= 0 or broker is None
                or broker not in self.connected_brokers):
            return
        meta = self.portfolio.meta
        last = meta.get("us_stocks_screened")
        today = datetime.now(ZoneInfo(config.TIMEZONE)).date()
        if last and (today - date.fromisoformat(last)).days < config.US_STOCK_RESCAN_DAYS:
            return
        meta["us_stocks_screened"] = today.isoformat()  # also on failure: no retry storm
        self.portfolio.save()
        try:
            picks = screener.find_liquid_us_stocks(
                broker, config.US_STOCK_COUNT, config.US_STOCK_MIN_PRICE,
                config.US_STOCK_MIN_MARKET_CAP_MUSD,
                config.US_STOCK_MIN_DOLLAR_VOLUME)
        except Exception as exc:
            log.warning("US stock screening failed (retry in %d days): %s",
                        config.US_STOCK_RESCAN_DAYS, exc)
            return
        current = list(meta.get("us_stocks", []))
        held = {sym for sym in current if sym in self.portfolio.positions}
        universe = screener.merge_universe(current, held, picks,
                                           config.US_STOCK_COUNT)
        for symbol in current:
            if symbol not in universe:
                self._mean_reversion().remove_symbol(symbol)
                self.brokers.pop(symbol, None)
        for symbol in universe:
            if symbol not in current:
                self._register_us_stock(symbol)
        if universe != current:
            self.portfolio.notify("🔎 US-aandelen bijgewerkt: "
                                  + (", ".join(universe) or "geen"))
        meta["us_stocks"] = universe
        self.portfolio.save()
        log.info("US stock universe: %s", ", ".join(universe) or "empty")

    # --- market data ----------------------------------------------------------

    def fetch_bars(self, symbol: str, timeframe_minutes: int) -> list[Bar]:
        """Recent candles, oldest first, excluding the in-progress one."""
        raw = self.brokers[symbol].fetch_bars(symbol, timeframe_minutes,
                                              config.BAR_FETCH_LIMIT)
        bucket_seconds = timeframe_minutes * 60
        current_bucket_start = (time.time() // bucket_seconds) * bucket_seconds
        return [bar for bar in raw if bar.ts < current_bucket_start]

    # --- trading --------------------------------------------------------------

    def check_stops(self) -> None:
        """Every loop: update trailing stops and close positions whose stop is hit."""
        changed = False
        for symbol, state in list(self.portfolio.positions.items()):
            broker = self.brokers[symbol]
            if broker not in self.connected_brokers:
                continue
            if not broker.market_open(symbol):
                continue  # cannot execute an exit while the market is closed
            price = broker.latest_price(symbol)
            if price is None:
                continue
            state.extra["last_price"] = price  # voor het dashboard
            self.risk.update_trailing_stop(state, price)
            changed = True
            if self.risk.stop_hit(state, price):
                kind = "trailing_stop" if state.trail_atr_mult else "stop_loss"
                self.portfolio.close_position(
                    symbol, f"{kind}: price {price:.4f} vs stop {state.stop_price:.4f}")
        if changed:
            self.portfolio.save()

    def evaluate_strategies(self) -> None:
        if self.paused:
            return  # gepauzeerd via dashboard: stops blijven wel actief
        now = time.time()
        for strategy in self.strategies:
            bucket_seconds = strategy.timeframe_minutes * 60
            bucket = int(now // bucket_seconds)
            for symbol in strategy.symbols:
                key = (strategy.name, symbol)
                if self.last_bucket.get(key) == bucket:
                    continue  # this candle was already handled
                if self.brokers[symbol] not in self.connected_brokers:
                    continue  # broker (nog) niet verbonden: instrument slaapt
                if not self.brokers[symbol].market_open(symbol):
                    continue  # no fresh bars and no fills outside market hours
                self.last_bucket[key] = bucket
                try:
                    bars = self.fetch_bars(symbol, strategy.timeframe_minutes)
                except Exception as exc:
                    log.warning("bar fetch failed for %s: %s", symbol, exc)
                    self.last_bucket.pop(key, None)  # retry next loop
                    continue
                if not bars:
                    continue
                self.refresh_position_atr(symbol, bars)
                signal = strategy.evaluate(symbol, bars,
                                           self.portfolio.position_side(symbol))
                if signal:
                    log.info("signal %s %s: %s", signal.action.upper(), symbol,
                             signal.reason)
                    self.execute(strategy, symbol, signal, bars)

    def refresh_position_atr(self, symbol: str, bars: list[Bar]) -> None:
        """Keep the trailing-stop distance in sync with current volatility."""
        state = self.portfolio.positions.get(symbol)
        if state is None:
            return
        atr = atr_last(bars, config.ATR_PERIOD)
        if atr and atr > 0:
            state.atr = atr
            self.portfolio.save()

    def _is_crypto(self, symbol: str) -> bool:
        return (symbol in config.CORRELATION_BLOCKED_SYMBOLS
                or (self._kraken_broker() is not None
                    and self.brokers.get(symbol) is self._kraken_broker()))

    def sleeve_exposure(self, crypto: bool) -> float:
        """Huidige totale positiewaarde (basisvaluta) van één potje."""
        total = 0.0
        for symbol, state in self.portfolio.positions.items():
            if self._is_crypto(symbol) != crypto:
                continue
            price = state.extra.get("last_price") or state.entry_price
            fx = 1.0
            try:
                fx = self.brokers[symbol].to_base_rate(symbol)
            except Exception:  # geen koers? entry-waarde is goed genoeg
                pass
            total += state.qty * price * fx
        return total

    def execute(self, strategy: Strategy, symbol: str, signal: Signal,
                bars: list[Bar]) -> None:
        broker = self.brokers[symbol]
        side = self.portfolio.position_side(symbol)

        if signal.action == "exit":
            if side:
                self.portfolio.close_position(symbol, signal.reason)
            return

        if signal.action == "short" and not broker.supports_short(symbol):
            # e.g. crypto spot, or a cash account without shorting rights:
            # a short signal then only means "get out of the long".
            if side == "long":
                self.portfolio.close_position(symbol, f"breakdown exit: {signal.reason}")
            else:
                log.info("short signal on %s ignored (%s does not allow shorts)",
                         symbol, broker.name)
            return

        if side == signal.action:
            return
        if side is not None:
            # Reversal: close the opposite position first.
            self.portfolio.close_position(symbol, f"reversal: {signal.reason}")

        is_crypto = self._is_crypto(symbol)
        if (signal.action == "long" and is_crypto
                and self.risk.correlation_blocks_crypto_long(
                    self.portfolio.position_sides())):
            log.info("correlation filter: %s and %s are both long, "
                     "blocking new %s long", *config.RISK_ON_PAIR, symbol)
            return

        atr = atr_last(bars, config.ATR_PERIOD)
        if not atr or atr <= 0:
            log.warning("no valid ATR for %s, skipping entry", symbol)
            return
        try:
            equity = self.total_equity()
            buying_power = broker.buying_power(symbol)
            fx = broker.to_base_rate(symbol)  # e.g. USD stock in a EUR account
        except Exception as exc:
            log.error("could not read account for %s entry: %s", symbol, exc)
            return
        budget = config.CRYPTO_BUDGET if is_crypto else config.STOCKS_BUDGET
        max_notional = None
        if budget > 0:
            remaining = budget - self.sleeve_exposure(is_crypto)
            if remaining <= 0:
                log.info("budget %s (%.2f) is vol: entry %s overgeslagen",
                         "crypto" if is_crypto else "aandelen/ETF's",
                         budget, symbol)
                return
            max_notional = remaining
        qty = self.risk.position_size(equity, bars[-1].close * fx, atr * fx,
                                      buying_power,
                                      fractional=broker.allows_fractional(symbol),
                                      max_notional=max_notional)
        self.portfolio.open_position(
            symbol, signal.action, qty, strategy.name, atr,
            stop_price_fn=lambda fill: self.risk.hard_stop_price(
                fill, atr, signal.action),
            trail_atr_mult=strategy.trail_atr_mult)

    # --- dashboard-besturing ------------------------------------------------------

    def process_close_all(self) -> None:
        """Noodstop vanaf het dashboard: verkoop alles waarvan de markt open is."""
        if not self.close_all_requested:
            return
        self.close_all_requested = False
        self.portfolio.notify("🛑 Noodstop via dashboard: alle posities worden gesloten")
        for symbol in list(self.portfolio.positions):
            if self.brokers[symbol].market_open(symbol):
                self.portfolio.close_position(symbol, "noodstop via dashboard")
            else:
                log.warning("noodstop: markt voor %s is dicht, positie blijft "
                            "open tot de beurs opent", symbol)
                self.portfolio.notify(f"⚠️ Noodstop: markt voor {symbol} is dicht; "
                                      "sluit zodra de beurs opent")

    def update_status(self) -> None:
        """Statussnapshot voor het dashboard (elke loop ververst)."""
        positions = []
        for symbol, state in self.portfolio.positions.items():
            price = state.extra.get("last_price")
            upnl = None
            if price:
                if state.direction == "long":
                    upnl = (price - state.entry_price) * state.qty
                else:
                    upnl = (state.entry_price - price) * state.qty
            positions.append({"symbol": symbol, "direction": state.direction,
                              "qty": state.qty, "entry": state.entry_price,
                              "stop": state.stop_price, "upnl": upnl,
                              "last_price": price,
                              "entry_time": state.entry_time,
                              "strategy": state.strategy})
        try:
            equity = self.total_equity() if self.connected_brokers else None
        except Exception as exc:
            log.debug("geen equity voor status: %s", exc)
            equity = self.status.get("equity")
        note = ""
        if self.pending_brokers:
            names = ", ".join(sorted(b.name for b in self.pending_brokers))
            note = f"wacht op verbinding met {names}…"
        # vermogensverloop voor het dashboard-grafiekje: 1 punt per minuut,
        # maximaal ~2 dagen (in geheugen; gaat verloren bij een herstart)
        history = list(self.status.get("equity_history", []))
        if equity is not None and (not history
                                   or time.time() - history[-1][0] >= 60):
            history = (history + [[round(time.time()), round(equity, 2)]])[-2880:]
        brokers = [{"name": b.name, "connected": b in self.connected_brokers}
                   for b in sorted(set(self.brokers.values()),
                                   key=lambda b: b.name)]
        try:
            sleeves = {
                "crypto": {"used": round(self.sleeve_exposure(True), 2),
                           "budget": config.CRYPTO_BUDGET or None},
                "stocks": {"used": round(self.sleeve_exposure(False), 2),
                           "budget": config.STOCKS_BUDGET or None},
            }
        except Exception as exc:
            log.debug("geen verdeling voor status: %s", exc)
            sleeves = self.status.get("sleeves", {})
        self.status = {"ts": time.time(), "equity": equity,
                       "positions": positions,
                       "universe": self.portfolio.meta.get("us_stocks", []),
                       "crypto_universe": self.portfolio.meta.get(
                           "crypto_universe", self._crypto_symbols()),
                       "brokers": brokers,
                       "sleeves": sleeves,
                       "equity_history": history,
                       "paused": self.paused, "market": config.BOT_MARKET,
                       "note": note}

    # --- daily P&L --------------------------------------------------------------

    def roll_daily_pnl(self) -> None:
        if not self.connected_brokers:
            return  # nog geen enkele broker: geen zinnig vermogen te loggen
        today = datetime.now(ZoneInfo(config.TIMEZONE)).date().isoformat()
        meta = self.portfolio.meta
        if meta.get("day") == today:
            return
        try:
            equity = self.total_equity()
        except Exception as exc:
            log.warning("could not read equity for daily P&L: %s", exc)
            return
        if meta.get("day"):
            start = float(meta["day_start_equity"])
            self.portfolio.log_daily_pnl(meta["day"], start, equity)
            log.info("daily P&L for %s: %.2f", meta["day"], equity - start)
            emoji = "🟢" if equity >= start else "🔴"
            self.portfolio.notify(f"{emoji} Dagresultaat {meta['day']}: "
                                  f"{equity - start:+.2f} (vermogen {equity:.2f})")
        meta["day"] = today
        meta["day_start_equity"] = equity
        self.portfolio.save()

    # --- main loop ----------------------------------------------------------------

    def run(self) -> None:
        log.info("market profile: %s — instruments: %s",
                 config.BOT_MARKET, ", ".join(config.INSTRUMENTS))
        self.try_connect_brokers()
        self.portfolio.reconcile()
        connected = ", ".join(sorted(b.name for b in self.connected_brokers)) or "nog geen"
        waiting = ", ".join(sorted(b.name for b in self.pending_brokers))
        self.portfolio.notify(
            f"🤖 Multi-bot gestart ({config.BOT_MARKET}) — verbonden: {connected}"
            + (f"; wacht op: {waiting}" if waiting else "")
            + " — instrumenten van verbonden brokers handelen direct mee")
        backoff = config.POLL_SECONDS
        while True:
            try:
                self.try_connect_brokers()
                self.process_close_all()
                self.maybe_screen_crypto()
                self.maybe_screen_us_stocks()
                self.roll_daily_pnl()
                self.check_stops()
                self.evaluate_strategies()
                self.update_status()
                backoff = config.POLL_SECONDS
            except KeyboardInterrupt:
                raise
            except (BrokerError, ConnectionError, OSError) as exc:
                log.error("broker error, retrying in %ds: %s", backoff, exc)
                if self.notifier:
                    self.notifier.send_error(f"⚠️ Broker-fout, opnieuw proberen "
                                             f"over {backoff}s: {exc}")
                backoff = min(backoff * 2, 300)
            except Exception as exc:
                log.exception("unexpected error, retrying in %ds", backoff)
                if self.notifier:
                    self.notifier.send_error(f"⚠️ Onverwachte fout: {exc}")
                backoff = min(backoff * 2, 300)
            time.sleep(backoff)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if config.BOT_MARKET == "us" and not (config.ALPACA_API_KEY
                                          and config.ALPACA_API_SECRET):
        sys.exit("BOT_MARKET=us requires ALPACA_API_KEY and ALPACA_API_SECRET "
                 "in .env (see .env.example)")
    brokers = build_brokers(config)
    notifier = TelegramNotifier.from_env()
    if notifier:
        notifier = PrefixedNotifier(notifier, config.TELEGRAM_PREFIX)
        log.info("Telegram notifications enabled (prefix: %s)",
                 config.TELEGRAM_PREFIX or "none")
    bot = Bot(brokers, notifier=notifier)
    if config.WEB_ENABLED:
        import secrets

        from bot.webapp import Dashboard
        code = config.WEB_CODE or secrets.token_hex(3)
        port = Dashboard(bot, config.TRADES_CSV, config.WEB_HOST,
                         config.WEB_PORT, code,
                         daily_pnl_csv=config.DAILY_PNL_CSV).start()
        log.info("📱 Dashboard: http://%s:%d — toegangscode voor de knoppen: %s",
                 config.WEB_HOST, port, code)
        if config.WEB_HOST == "127.0.0.1":
            log.info("   (op afstand meekijken: ssh -L %d:localhost:%d root@<server>,"
                     " daarna http://localhost:%d openen)", port, port, port)
    try:
        bot.run()
    except KeyboardInterrupt:
        bot.portfolio.save()
        log.info("stopped by user; state saved (open positions stay open)")


if __name__ == "__main__":
    main()
