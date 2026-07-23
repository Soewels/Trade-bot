"""Unit tests: the portfolio sends a Telegram-style message on every
open/close, and never breaks trading when no notifier is configured."""

import os
import tempfile
import unittest

from bot.brokers.base import Broker, Fill
from bot.portfolio import Portfolio


class StubBroker(Broker):
    name = "stub"

    def submit_market_order(self, symbol, side, qty):
        return Fill(price=100.0, qty=qty)

    def position_symbols(self):
        return None


class StubNotifier:
    def __init__(self):
        self.messages = []

    def send(self, text):
        self.messages.append(text)
        return True


def make_portfolio(notifier):
    fd, state_path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.unlink(state_path)
    tmp = tempfile.mkdtemp()
    broker = StubBroker()
    return Portfolio({"SXR8": broker}, state_path,
                     os.path.join(tmp, "trades.csv"),
                     os.path.join(tmp, "daily.csv"), notifier=notifier)


class PrefixedNotifierTests(unittest.TestCase):
    def test_prefix_is_added_to_all_messages(self):
        from bot.main import PrefixedNotifier

        class Inner:
            def __init__(self):
                self.sent, self.errors = [], []

            def send(self, text):
                self.sent.append(text)
                return True

            def send_error(self, text):
                self.errors.append(text)
                return True

        inner = Inner()
        notifier = PrefixedNotifier(inner, "[Multi-bot]")
        notifier.send("gekocht")
        notifier.send_error("storing")
        self.assertEqual(inner.sent, ["[Multi-bot] gekocht"])
        self.assertEqual(inner.errors, ["[Multi-bot] storing"])
        # empty prefix leaves messages untouched
        PrefixedNotifier(inner, "").send("kaal")
        self.assertEqual(inner.sent[-1], "kaal")


class NotificationTests(unittest.TestCase):
    def test_open_and_close_send_messages(self):
        notifier = StubNotifier()
        portfolio = make_portfolio(notifier)
        portfolio.open_position("SXR8", "long", 10, "mean_reversion", atr=2.0,
                                stop_price_fn=lambda fill: fill - 2.0)
        self.assertEqual(len(notifier.messages), 1)
        self.assertIn("LONG SXR8", notifier.messages[0])
        self.assertIn("stop 98.0000", notifier.messages[0])
        portfolio.close_position("SXR8", "test-exit")
        self.assertEqual(len(notifier.messages), 2)
        self.assertIn("gesloten", notifier.messages[1])
        self.assertIn("test-exit", notifier.messages[1])

    def test_zero_qty_skip_sends_nothing(self):
        notifier = StubNotifier()
        portfolio = make_portfolio(notifier)
        portfolio.open_position("SXR8", "long", 0, "mean_reversion", atr=2.0,
                                stop_price_fn=lambda fill: fill - 2.0)
        self.assertEqual(notifier.messages, [])

    def test_works_without_notifier(self):
        portfolio = make_portfolio(None)
        state = portfolio.open_position("SXR8", "long", 10, "mean_reversion",
                                        atr=2.0,
                                        stop_price_fn=lambda fill: fill - 2.0)
        self.assertIsNotNone(state)
        self.assertIsNotNone(portfolio.close_position("SXR8", "test-exit"))


if __name__ == "__main__":
    unittest.main()
