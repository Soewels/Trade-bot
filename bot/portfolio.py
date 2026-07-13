"""Portfolio: Alpaca account/order access, position bookkeeping and CSV logs.

Keeps its own PositionState per instrument (entry, stop, trailing data,
which strategy owns it) in a JSON file so the bot is restart-safe, and
reconciles that state against the real Alpaca positions on startup.
"""

import csv
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional

from .models import PositionState

log = logging.getLogger("alpaca_bot.portfolio")

TRADES_FIELDS = ["timestamp", "instrument", "direction", "entry_price",
                 "exit_price", "pnl", "position_size"]
DAILY_FIELDS = ["date", "start_equity", "end_equity", "pnl"]


def normalize_symbol(symbol: str) -> str:
    """Alpaca reports crypto positions as 'BTCUSD' while orders use 'BTC/USD'."""
    return symbol.replace("/", "")


class Portfolio:
    def __init__(self, api, crypto_symbols: set[str], state_file: str,
                 trades_csv: str, daily_pnl_csv: str):
        self.api = api
        self.crypto_symbols = set(crypto_symbols)
        self.state_file = state_file
        self.trades_csv = trades_csv
        self.daily_pnl_csv = daily_pnl_csv
        self.positions: dict[str, PositionState] = {}
        self.meta: dict = {}          # day-rollover bookkeeping etc.
        self._load_state()

    # --- account ------------------------------------------------------------

    def equity(self) -> float:
        return float(self.api.get_account().equity)

    def buying_power(self, symbol: str) -> float:
        account = self.api.get_account()
        if self.is_crypto(symbol):
            # Crypto is non-marginable: only settled cash can be used.
            value = getattr(account, "non_marginable_buying_power", None) or account.cash
            return float(value)
        return float(account.buying_power)

    def is_crypto(self, symbol: str) -> bool:
        return symbol in self.crypto_symbols

    def position_side(self, symbol: str) -> Optional[str]:
        state = self.positions.get(symbol)
        return state.direction if state else None

    def position_sides(self) -> dict[str, Optional[str]]:
        return {sym: state.direction for sym, state in self.positions.items()}

    # --- startup reconciliation ----------------------------------------------

    def reconcile(self) -> None:
        """Drop state for positions that no longer exist at Alpaca and warn
        about live positions the bot has no state for (it will not touch those)."""
        try:
            live = {normalize_symbol(p.symbol): p for p in self.api.list_positions()}
        except Exception as exc:
            log.warning("could not list Alpaca positions during reconcile: %s", exc)
            return
        for symbol in list(self.positions):
            if normalize_symbol(symbol) not in live:
                log.warning("state for %s has no matching Alpaca position; dropping", symbol)
                del self.positions[symbol]
        known = {normalize_symbol(s) for s in self.positions}
        for sym, pos in live.items():
            if sym not in known:
                log.warning("Alpaca position %s (%s %s) is not managed by this bot; "
                            "leaving it alone", sym, pos.side, pos.qty)
        self._save_state()

    # --- orders ---------------------------------------------------------------

    def open_position(self, symbol: str, direction: str, qty: float,
                      strategy: str, atr: float, stop_price_fn,
                      trail_atr_mult: Optional[float] = None) -> Optional[PositionState]:
        """Submit a market order to open a position and wait for the fill.

        `stop_price_fn(fill_price)` computes the hard stop from the actual
        fill price, so the 1%-of-equity risk is anchored to reality, not to
        the last candle close.
        """
        if qty <= 0:
            log.info("skip %s %s: computed size is 0", direction, symbol)
            return None
        side = "buy" if direction == "long" else "sell"
        tif = "gtc" if self.is_crypto(symbol) else "day"
        order = self.api.submit_order(symbol=symbol, qty=qty, side=side,
                                      type="market", time_in_force=tif)
        filled = self._wait_for_fill(order.id)
        if filled is None:
            log.error("order %s for %s did not fill; cancelling", order.id, symbol)
            self._try_cancel(order.id)
            return None
        fill_price = float(filled.filled_avg_price)
        fill_qty = float(filled.filled_qty)
        state = PositionState(
            symbol=symbol,
            direction=direction,
            qty=fill_qty,
            entry_price=fill_price,
            entry_time=datetime.now(timezone.utc).isoformat(),
            strategy=strategy,
            atr=atr,
            stop_price=stop_price_fn(fill_price),
            trail_atr_mult=trail_atr_mult,
            peak_price=fill_price,
        )
        self.positions[symbol] = state
        self._save_state()
        log.info("OPENED %s %s qty=%s @ %.4f stop=%.4f (%s)",
                 direction.upper(), symbol, fill_qty, fill_price,
                 state.stop_price, strategy)
        return state

    def close_position(self, symbol: str, reason: str) -> Optional[float]:
        """Close the bot-managed position with a market order; returns realized P&L."""
        state = self.positions.get(symbol)
        if state is None:
            return None
        side = "sell" if state.direction == "long" else "buy"
        tif = "gtc" if self.is_crypto(symbol) else "day"
        order = self.api.submit_order(symbol=symbol, qty=state.qty, side=side,
                                      type="market", time_in_force=tif)
        filled = self._wait_for_fill(order.id)
        if filled is None:
            log.error("close order %s for %s did not fill; keeping state", order.id, symbol)
            return None
        exit_price = float(filled.filled_avg_price)
        if state.direction == "long":
            pnl = (exit_price - state.entry_price) * state.qty
        else:
            pnl = (state.entry_price - exit_price) * state.qty
        self._log_trade(state, exit_price, pnl)
        del self.positions[symbol]
        self._save_state()
        log.info("CLOSED %s %s qty=%s @ %.4f pnl=%.2f (%s)",
                 state.direction.upper(), symbol, state.qty, exit_price, pnl, reason)
        return pnl

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
