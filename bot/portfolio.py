"""Portfolio: order routing via brokers, position bookkeeping and CSV logs.

Keeps its own PositionState per instrument (entry, stop, trailing data,
which strategy owns it) in a JSON file so the bot is restart-safe, and
reconciles that state against the brokers' real positions on startup.
"""

import csv
import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from .brokers.base import Broker, BrokerError
from .models import PositionState

log = logging.getLogger("alpaca_bot.portfolio")

TRADES_FIELDS = ["timestamp", "instrument", "direction", "entry_price",
                 "exit_price", "pnl", "position_size"]
DAILY_FIELDS = ["date", "start_equity", "end_equity", "pnl"]


class Portfolio:
    def __init__(self, brokers: dict[str, Broker], state_file: str,
                 trades_csv: str, daily_pnl_csv: str, notifier=None):
        """`brokers` maps each tradable symbol to its broker instance.
        `notifier` is an optional TelegramNotifier-like object (.send())."""
        self.brokers = brokers
        self.state_file = state_file
        self.trades_csv = trades_csv
        self.daily_pnl_csv = daily_pnl_csv
        self.notifier = notifier
        self.positions: dict[str, PositionState] = {}
        self.meta: dict = {}          # day-rollover bookkeeping etc.
        self._load_state()

    def notify(self, text: str) -> None:
        if self.notifier:
            self.notifier.send(text)

    def broker_for(self, symbol: str) -> Broker:
        return self.brokers[symbol]

    # --- account ------------------------------------------------------------

    def total_equity(self) -> float:
        """Combined account value across all connected brokers."""
        return sum(broker.equity() for broker in set(self.brokers.values()))

    def position_side(self, symbol: str) -> Optional[str]:
        state = self.positions.get(symbol)
        return state.direction if state else None

    def position_sides(self) -> dict[str, Optional[str]]:
        return {sym: state.direction for sym, state in self.positions.items()}

    # --- startup reconciliation ----------------------------------------------

    def reconcile(self) -> None:
        """Drop state for positions that no longer exist at the broker."""
        for broker in set(self.brokers.values()):
            live = broker.position_symbols()
            if live is None:
                continue  # broker cannot report positions; trust our state
            for symbol in list(self.positions):
                if self.brokers.get(symbol) is broker and symbol not in live:
                    log.warning("state for %s has no matching %s position; "
                                "dropping", symbol, broker.name)
                    del self.positions[symbol]
        self._save_state()

    # --- orders ---------------------------------------------------------------

    def open_position(self, symbol: str, direction: str, qty: float,
                      strategy: str, atr: float, stop_price_fn,
                      trail_atr_mult: Optional[float] = None) -> Optional[PositionState]:
        """Open a position with a market order and wait for the fill.

        `stop_price_fn(fill_price)` computes the hard stop from the actual
        fill price, so the 1%-of-equity risk is anchored to reality, not to
        the last candle close.
        """
        if qty <= 0:
            log.info("skip %s %s: computed size is 0", direction, symbol)
            return None
        side = "buy" if direction == "long" else "sell"
        try:
            fill = self.broker_for(symbol).submit_market_order(symbol, side, qty)
        except BrokerError as exc:
            log.error("could not open %s %s: %s", direction, symbol, exc)
            return None
        state = PositionState(
            symbol=symbol,
            direction=direction,
            qty=fill.qty,
            entry_price=fill.price,
            entry_time=datetime.now(timezone.utc).isoformat(),
            strategy=strategy,
            atr=atr,
            stop_price=stop_price_fn(fill.price),
            trail_atr_mult=trail_atr_mult,
            peak_price=fill.price,
        )
        self.positions[symbol] = state
        self._save_state()
        log.info("OPENED %s %s qty=%s @ %.4f stop=%.4f (%s)",
                 direction.upper(), symbol, fill.qty, fill.price,
                 state.stop_price, strategy)
        emoji = "📈" if direction == "long" else "📉"
        self.notify(f"{emoji} {direction.upper()} {symbol}: {fill.qty:g} "
                    f"@ {fill.price:.4f} (stop {state.stop_price:.4f}) — {strategy}")
        return state

    def close_position(self, symbol: str, reason: str) -> Optional[float]:
        """Close the bot-managed position with a market order; returns realized P&L."""
        state = self.positions.get(symbol)
        if state is None:
            return None
        side = "sell" if state.direction == "long" else "buy"
        try:
            fill = self.broker_for(symbol).submit_market_order(symbol, side, state.qty)
        except BrokerError as exc:
            log.error("could not close %s (keeping state): %s", symbol, exc)
            return None
        if state.direction == "long":
            pnl = (fill.price - state.entry_price) * state.qty
        else:
            pnl = (state.entry_price - fill.price) * state.qty
        self._log_trade(state, fill.price, pnl)
        del self.positions[symbol]
        self._save_state()
        log.info("CLOSED %s %s qty=%s @ %.4f pnl=%.2f (%s)",
                 state.direction.upper(), symbol, state.qty, fill.price, pnl, reason)
        emoji = "✅" if pnl >= 0 else "🔻"
        self.notify(f"{emoji} {symbol} {state.direction} gesloten @ {fill.price:.4f}: "
                    f"{pnl:+.2f} ({reason})")
        return pnl

    # --- CSV logging -----------------------------------------------------------

    def _log_trade(self, state: PositionState, exit_price: float, pnl: float) -> None:
        self._append_csv(self.trades_csv, TRADES_FIELDS, {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "instrument": state.symbol,
            "direction": state.direction,
            "entry_price": f"{state.entry_price:.6f}",
            "exit_price": f"{exit_price:.6f}",
            "pnl": f"{pnl:.2f}",
            "position_size": state.qty,
        })

    def log_daily_pnl(self, date_str: str, start_equity: float, end_equity: float) -> None:
        self._append_csv(self.daily_pnl_csv, DAILY_FIELDS, {
            "date": date_str,
            "start_equity": f"{start_equity:.2f}",
            "end_equity": f"{end_equity:.2f}",
            "pnl": f"{end_equity - start_equity:.2f}",
        })

    @staticmethod
    def _append_csv(path: str, fields: list[str], row: dict) -> None:
        new_file = not os.path.exists(path)
        with open(path, "a", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            if new_file:
                writer.writeheader()
            writer.writerow(row)

    # --- state persistence ------------------------------------------------------

    def _load_state(self) -> None:
        if not os.path.exists(self.state_file):
            return
        try:
            with open(self.state_file) as handle:
                data = json.load(handle)
            self.positions = {
                sym: PositionState.from_dict(raw)
                for sym, raw in data.get("positions", {}).items()
            }
            self.meta = data.get("meta", {})
            if self.positions:
                log.info("restored state for: %s", ", ".join(self.positions))
        except (json.JSONDecodeError, TypeError, KeyError) as exc:
            log.error("could not read state file %s: %s", self.state_file, exc)

    def _save_state(self) -> None:
        data = {
            "positions": {sym: st.to_dict() for sym, st in self.positions.items()},
            "meta": self.meta,
        }
        tmp = self.state_file + ".tmp"
        with open(tmp, "w") as handle:
            json.dump(data, handle, indent=2)
        os.replace(tmp, self.state_file)

    def save(self) -> None:
        self._save_state()
