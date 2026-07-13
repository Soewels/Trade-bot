"""Alpaca multi-instrument trading bot — main loop.

Runs three strategies on five instruments:
  - mean reversion on SPY & QQQ (15-minute candles)
  - momentum breakout on BTC/USD (1-hour candles)
  - trend following on GLD & USO (4-hour candles)

The loop wakes every POLL_SECONDS to check hard/trailing stops against the
latest price, and evaluates each strategy whenever one of its candles has
closed. Equities only trade during regular market hours; crypto runs 24/7.

Run from the project root:  python -m bot.main
"""

import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from bot.indicators import atr_last
from bot.models import Bar, Signal
from bot.portfolio import Portfolio
from bot.risk_manager import RiskManager
from bot.strategies import (MeanReversionStrategy, MomentumBreakoutStrategy,
                            TrendFollowingStrategy)
from bot.strategies.base import Strategy

try:
    from alpaca_trade_api.rest import REST, APIError, TimeFrame, TimeFrameUnit
except ImportError:  # pragma: no cover
    sys.exit("alpaca-trade-api is not installed. Run: pip install alpaca-trade-api")

log = logging.getLogger("alpaca_bot")

NY_TZ = ZoneInfo("America/New_York")


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


def alpaca_timeframe(minutes: int) -> "TimeFrame":
    if minutes % 60 == 0:
        return TimeFrame(minutes // 60, TimeFrameUnit.Hour)
    return TimeFrame(minutes, TimeFrameUnit.Minute)


class Bot:
    def __init__(self, api: REST):
        self.api = api
        self.strategies = build_strategies()
        self.risk = RiskManager(config.RISK_PER_TRADE, config.MAX_NOTIONAL_FRACTION)
        self.portfolio = Portfolio(api, config.CRYPTO_SYMBOLS, config.STATE_FILE,
                                   config.TRADES_CSV, config.DAILY_PNL_CSV)
        # last evaluated candle bucket per (strategy, symbol)
        self.last_bucket: dict[tuple[str, str], int] = {}

    # --- market data ----------------------------------------------------------

    def fetch_bars(self, symbol: str, timeframe_minutes: int) -> list[Bar]:
        """Fetch recent candles, oldest first, excluding the in-progress one."""
        timeframe = alpaca_timeframe(timeframe_minutes)
        if self.portfolio.is_crypto(symbol):
            raw = self.api.get_crypto_bars(symbol, timeframe,
                                           limit=config.BAR_FETCH_LIMIT)
        else:
            raw = self.api.get_bars(symbol, timeframe, limit=config.BAR_FETCH_LIMIT,
                                    feed=config.ALPACA_DATA_FEED)
        bucket_seconds = timeframe_minutes * 60
        current_bucket_start = (time.time() // bucket_seconds) * bucket_seconds
        bars = []
        for item in raw:
            ts = item.t.timestamp()
            if ts >= current_bucket_start:
                continue  # candle still forming
            bars.append(Bar(ts=ts, open=float(item.o), high=float(item.h),
                            low=float(item.l), close=float(item.c),
                            volume=float(item.v)))
        return bars

    def latest_price(self, symbol: str) -> Optional[float]:
        try:
            if self.portfolio.is_crypto(symbol):
                raw = self.api.get_crypto_bars(symbol, TimeFrame.Minute, limit=1)
                bars = list(raw)
                return float(bars[-1].c) if bars else None
            return float(self.api.get_latest_trade(symbol).price)
        except Exception as exc:
            log.warning("no latest price for %s: %s", symbol, exc)
            return None

    def market_open(self) -> bool:
        try:
            return bool(self.api.get_clock().is_open)
        except Exception as exc:
            log.warning("could not read market clock, treating as closed: %s", exc)
            return False

    # --- trading --------------------------------------------------------------

    def check_stops(self, equities_open: bool) -> None:
        """Every loop: update trailing stops and close positions whose stop is hit."""
        changed = False
        for symbol, state in list(self.portfolio.positions.items()):
            if not self.portfolio.is_crypto(symbol) and not equities_open:
                continue  # cannot execute an exit while the market is closed
            price = self.latest_price(symbol)
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

    def evaluate_strategies(self, equities_open: bool) -> None:
        now = time.time()
        for strategy in self.strategies:
            bucket_seconds = strategy.timeframe_minutes * 60
            bucket = int(now // bucket_seconds)
            for symbol in strategy.symbols:
                key = (strategy.name, symbol)
                if self.last_bucket.get(key) == bucket:
                    continue  # this candle was already handled
                if not self.portfolio.is_crypto(symbol) and not equities_open:
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
        side = self.portfolio.position_side(symbol)

        if signal.action == "exit":
            if side:
                self.portfolio.close_position(symbol, signal.reason)
            return

        if signal.action == "short" and self.portfolio.is_crypto(symbol):
            # Alpaca does not support shorting crypto: treat as exit-long.
            if side == "long":
                self.portfolio.close_position(symbol, f"breakdown exit: {signal.reason}")
            else:
                log.info("short signal on %s ignored (crypto shorts not supported)",
                         symbol)
            return

        if side == signal.action:
            return
        if side is not None:
            # Reversal: close the opposite position first.
            self.portfolio.close_position(symbol, f"reversal: {signal.reason}")

        if (signal.action == "long" and self.portfolio.is_crypto(symbol)
                and self.risk.correlation_blocks_crypto_long(
                    self.portfolio.position_sides())):
            log.info("correlation filter: SPY and QQQ are both long, "
                     "blocking new %s long", symbol)
            return

        atr = atr_last(bars, config.ATR_PERIOD)
        if not atr or atr <= 0:
            log.warning("no valid ATR for %s, skipping entry", symbol)
            return
        try:
            equity = self.portfolio.equity()
            buying_power = self.portfolio.buying_power(symbol)
        except Exception as exc:
            log.error("could not read account for %s entry: %s", symbol, exc)
            return
        qty = self.risk.position_size(equity, bars[-1].close, atr, buying_power,
                                      fractional=self.portfolio.is_crypto(symbol))
        self.portfolio.open_position(
            symbol, signal.action, qty, strategy.name, atr,
            stop_price_fn=lambda fill: self.risk.hard_stop_price(
                fill, atr, signal.action),
            trail_atr_mult=strategy.trail_atr_mult)

    # --- daily P&L --------------------------------------------------------------

    def roll_daily_pnl(self) -> None:
        today = datetime.now(NY_TZ).date().isoformat()
        meta = self.portfolio.meta
        if meta.get("day") == today:
            return
        try:
            equity = self.portfolio.equity()
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
        account = self.api.get_account()
        log.info("connected to Alpaca (%s): equity=%s buying_power=%s",
                 config.ALPACA_BASE_URL, account.equity, account.buying_power)
        self.portfolio.reconcile()
        backoff = config.POLL_SECONDS
        while True:
            try:
                equities_open = self.market_open()
                self.roll_daily_pnl()
                self.check_stops(equities_open)
                self.evaluate_strategies(equities_open)
                backoff = config.POLL_SECONDS
            except KeyboardInterrupt:
                raise
            except (APIError, ConnectionError, OSError) as exc:
                log.error("API error, retrying in %ds: %s", backoff, exc)
                backoff = min(backoff * 2, 300)
            except Exception:
                log.exception("unexpected error, retrying in %ds", backoff)
                backoff = min(backoff * 2, 300)
            time.sleep(backoff)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not config.ALPACA_API_KEY or not config.ALPACA_API_SECRET:
        sys.exit("Set ALPACA_API_KEY and ALPACA_API_SECRET in .env "
                 "(see .env.example)")
    api = REST(config.ALPACA_API_KEY, config.ALPACA_API_SECRET,
               config.ALPACA_BASE_URL)
    bot = Bot(api)
    try:
        bot.run()
    except KeyboardInterrupt:
        bot.portfolio.save()
        log.info("stopped by user; state saved (open positions stay open)")


if __name__ == "__main__":
    main()
