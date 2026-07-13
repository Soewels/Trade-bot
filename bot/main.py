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
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from bot.brokers import BrokerError, build_brokers
from bot.indicators import atr_last
from bot.models import Bar, Signal
from bot.portfolio import Portfolio
from bot.risk_manager import RiskManager
from bot.strategies import (MeanReversionStrategy, MomentumBreakoutStrategy,
                            TrendFollowingStrategy)
from bot.strategies.base import Strategy

log = logging.getLogger("alpaca_bot")


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
    def __init__(self, brokers):
        """`brokers` maps each tradable symbol to a broker instance."""
        self.brokers = brokers
        self.strategies = build_strategies()
        self.risk = RiskManager(config.RISK_PER_TRADE, config.MAX_NOTIONAL_FRACTION,
                                risk_on_pair=config.RISK_ON_PAIR)
        self.portfolio = Portfolio(brokers, config.STATE_FILE,
                                   config.TRADES_CSV, config.DAILY_PNL_CSV)
        # last evaluated candle bucket per (strategy, symbol)
        self.last_bucket: dict[tuple[str, str], int] = {}

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
            if not broker.market_open(symbol):
                continue  # cannot execute an exit while the market is closed
            price = broker.latest_price(symbol)
            if price is None:
                continue
            self.risk.update_trailing_stop(state, price)
            changed = True
            if self.risk.stop_hit(state, price):
                kind = "trailing_stop" if state.trail_atr_mult else "stop_loss"
                self.portfolio.close_position(
                    symbol, f"{kind}: price {price:.4f} vs stop {state.stop_price:.4f}")
        if changed:
            self.portfolio.save()

    def evaluate_strategies(self) -> None:
        now = time.time()
        for strategy in self.strategies:
            bucket_seconds = strategy.timeframe_minutes * 60
            bucket = int(now // bucket_seconds)
            for symbol in strategy.symbols:
                key = (strategy.name, symbol)
                if self.last_bucket.get(key) == bucket:
                    continue  # this candle was already handled
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

        if (signal.action == "long" and symbol == config.CORRELATION_BLOCKED_SYMBOL
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
            equity = self.portfolio.total_equity()
            buying_power = broker.buying_power(symbol)
        except Exception as exc:
            log.error("could not read account for %s entry: %s", symbol, exc)
            return
        qty = self.risk.position_size(equity, bars[-1].close, atr, buying_power,
                                      fractional=broker.allows_fractional(symbol))
        self.portfolio.open_position(
            symbol, signal.action, qty, strategy.name, atr,
            stop_price_fn=lambda fill: self.risk.hard_stop_price(
                fill, atr, signal.action),
            trail_atr_mult=strategy.trail_atr_mult)

    # --- daily P&L --------------------------------------------------------------

    def roll_daily_pnl(self) -> None:
        today = datetime.now(ZoneInfo(config.TIMEZONE)).date().isoformat()
        meta = self.portfolio.meta
        if meta.get("day") == today:
            return
        try:
            equity = self.portfolio.total_equity()
        except Exception as exc:
            log.warning("could not read equity for daily P&L: %s", exc)
            return
        if meta.get("day"):
            self.portfolio.log_daily_pnl(meta["day"],
                                         float(meta["day_start_equity"]), equity)
            log.info("daily P&L for %s: %.2f",
                     meta["day"], equity - float(meta["day_start_equity"]))
        meta["day"] = today
        meta["day_start_equity"] = equity
        self.portfolio.save()

    # --- main loop ----------------------------------------------------------------

    def run(self) -> None:
        log.info("market profile: %s — instruments: %s",
                 config.BOT_MARKET, ", ".join(config.INSTRUMENTS))
        log.info("total equity across brokers: %.2f", self.portfolio.total_equity())
        self.portfolio.reconcile()
        backoff = config.POLL_SECONDS
        while True:
            try:
                self.roll_daily_pnl()
                self.check_stops()
                self.evaluate_strategies()
                backoff = config.POLL_SECONDS
            except KeyboardInterrupt:
                raise
            except (BrokerError, ConnectionError, OSError) as exc:
                log.error("broker error, retrying in %ds: %s", backoff, exc)
                backoff = min(backoff * 2, 300)
            except Exception:
                log.exception("unexpected error, retrying in %ds", backoff)
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
    for broker in set(brokers.values()):
        broker.connect()
    bot = Bot(brokers)
    try:
        bot.run()
    except KeyboardInterrupt:
        bot.portfolio.save()
        log.info("stopped by user; state saved (open positions stay open)")


if __name__ == "__main__":
    main()
