"""Tests voor het detecteren van weggevallen brokerverbindingen: een dode
broker mag de andere brokers nooit blokkeren en moet automatisch opnieuw
verbonden worden."""

import os
import tempfile
import unittest

import config
from tests.test_dashboard import StubBroker


class DyingBroker(StubBroker):
    """Broker die na `fail_after` equity-opvragen de verbinding verliest."""

    name = "dying"

    def __init__(self, fail_after: int):
        super().__init__()
        self.calls = 0
        self.fail_after = fail_after
        self.disconnects = 0
        self.reconnect_ok = False

    def equity(self):
        self.calls += 1
        if self.calls > self.fail_after:
            raise ConnectionError("gateway herstart")
        return 1_000_000.0

    def disconnect(self):
        self.disconnects += 1

    def connect(self):
        if not self.reconnect_ok:
            raise ConnectionError("gateway nog steeds weg")
        self.calls = 0  # verbinding is terug: equity werkt weer


def make_two_broker_bot(good, bad):
    tmp = tempfile.mkdtemp()
    overrides = {"STATE_FILE": os.path.join(tmp, "state.json"),
                 "TRADES_CSV": os.path.join(tmp, "trades.csv"),
                 "DAILY_PNL_CSV": os.path.join(tmp, "daily.csv")}
    originals = {key: getattr(config, key) for key in overrides}
    for key, value in overrides.items():
        setattr(config, key, value)
    try:
        from bot.main import Bot
        bot = Bot({"GOOD/EUR": good, "BAD": bad})
        bot.connected_brokers = {good, bad}
        bot.pending_brokers = set()
        return bot
    finally:
        for key, value in originals.items():
            setattr(config, key, value)


class BrokerLossTests(unittest.TestCase):
    def setUp(self):
        self.good = StubBroker()          # equity: 10.000
        self.bad = DyingBroker(fail_after=1)
        self.bot = make_two_broker_bot(self.good, self.bad)

    def test_healthy_brokers_sum_normally(self):
        self.assertEqual(self.bot.total_equity(), 1_010_000.0)

    def test_dead_broker_uses_cache_and_is_marked_down(self):
        self.bot.total_equity()           # 1e call: ok, vult de cache
        total = self.bot.total_equity()   # 2e call: dying valt uit
        self.assertEqual(total, 1_010_000.0)  # laatst bekende stand telt mee
        self.assertNotIn(self.bad, self.bot.connected_brokers)
        self.assertIn(self.bad, self.bot.pending_brokers)
        self.assertEqual(self.bad.disconnects, 1)  # netjes opgeruimd
        # de laatst bekende stand blijft meetellen zolang de broker weg is
        # (het geld staat er nog); de gezonde broker handelt gewoon door
        self.assertEqual(self.bot.total_equity(), 1_010_000.0)

    def test_dead_broker_without_cache_is_skipped(self):
        self.bad.fail_after = 0           # meteen stuk, nooit een stand gezien
        self.assertEqual(self.bot.total_equity(), 10_000.0)
        self.assertNotIn(self.bad, self.bot.connected_brokers)

    def test_reconnect_restores_broker(self):
        self.bot.total_equity()
        self.bot.total_equity()           # markeert dying als weggevallen
        self.bot.try_connect_brokers()    # gateway nog weg: blijft pending
        self.assertIn(self.bad, self.bot.pending_brokers)
        self.bad.reconnect_ok = True
        self.bot._next_connect_try = 0.0  # geen backoff-wachttijd in de test
        self.bot.try_connect_brokers()
        self.assertIn(self.bad, self.bot.connected_brokers)
        self.assertEqual(self.bot.total_equity(), 1_010_000.0)

    def test_marking_unknown_broker_is_noop(self):
        outsider = StubBroker()
        self.bot.mark_broker_down(outsider, RuntimeError("x"))
        self.assertNotIn(outsider, self.bot.pending_brokers)


if __name__ == "__main__":
    unittest.main()
