"""Tests voor het mobiele dashboard: HTTP-endpoints, toegangscode,
pauze en noodstop. Draait volledig lokaal (poort 0, stub-brokers)."""

import json
import os
import tempfile
import time
import unittest
import urllib.request

import config
from bot.brokers.base import Broker, Fill
from bot.models import Bar
from bot.webapp import Dashboard


class StubBroker(Broker):
    name = "stub"

    def __init__(self, market_is_open=True):
        self._open = market_is_open
        self.closed_orders = []

    def equity(self):
        return 10_000.0

    def buying_power(self, symbol):
        return 10_000.0

    def fetch_bars(self, symbol, timeframe_minutes, limit):
        raise AssertionError("mag niet gebeuren in deze test")

    def latest_price(self, symbol):
        return 100.0

    def market_open(self, symbol):
        return self._open

    def supports_short(self, symbol):
        return False

    def allows_fractional(self, symbol):
        return True

    def submit_market_order(self, symbol, side, qty):
        self.closed_orders.append((symbol, side, qty))
        return Fill(price=100.0, qty=qty)

    def position_symbols(self):
        return None


def make_bot(broker):
    """Echte Bot met tijdelijke bestandspaden en stub-brokers."""
    tmp = tempfile.mkdtemp()
    overrides = {"STATE_FILE": os.path.join(tmp, "state.json"),
                 "TRADES_CSV": os.path.join(tmp, "trades.csv"),
                 "DAILY_PNL_CSV": os.path.join(tmp, "daily.csv")}
    originals = {key: getattr(config, key) for key in overrides}
    for key, value in overrides.items():
        setattr(config, key, value)
    try:
        from bot.main import Bot
        brokers = {sym: broker for sym in config.INSTRUMENTS}
        bot = Bot(brokers)
        # in tests gelden alle brokers als verbonden
        bot.connected_brokers = set(brokers.values())
        bot.pending_brokers = set()
        return bot
    finally:
        for key, value in originals.items():
            setattr(config, key, value)


class ConnectRetryTests(unittest.TestCase):
    def test_connect_keeps_retrying_until_success(self):
        class FlakyBroker(StubBroker):
            def __init__(self):
                super().__init__()
                self.attempts = 0

            def connect(self):
                self.attempts += 1
                if self.attempts < 3:
                    raise ConnectionRefusedError("gateway nog niet ingelogd")

        broker = FlakyBroker()
        bot = make_bot(broker)
        bot.pending_brokers = {broker}
        bot.connected_brokers = set()
        bot._connect_delay = 0.0
        for _ in range(5):
            bot.try_connect_brokers()
        self.assertEqual(broker.attempts, 3)  # 2x mislukt, 3e keer gelukt
        self.assertIn(broker, bot.connected_brokers)
        self.assertFalse(bot.pending_brokers)
        bot.update_status()
        self.assertEqual(bot.status["note"], "")

    def test_pending_broker_pauses_its_instruments_only(self):
        broker = StubBroker()
        bot = make_bot(broker)
        bot.pending_brokers = {broker}
        bot.connected_brokers = set()
        bot.evaluate_strategies()   # zou anders fetch_bars -> AssertionError geven
        bot.update_status()
        self.assertIn("wacht op verbinding", bot.status["note"])


class BotControlTests(unittest.TestCase):
    def test_pause_blocks_new_evaluations(self):
        bot = make_bot(StubBroker())
        bot.paused = True
        bot.evaluate_strategies()  # zou fetch_bars aanroepen -> AssertionError

    def test_close_all_sells_open_positions_when_market_open(self):
        broker = StubBroker(market_is_open=True)
        bot = make_bot(broker)
        symbol = next(iter(config.INSTRUMENTS))
        bot.portfolio.open_position(symbol, "long", 1.0, "test", atr=2.0,
                                    stop_price_fn=lambda fill: fill - 2.0)
        bot.close_all_requested = True
        bot.process_close_all()
        self.assertEqual(bot.portfolio.positions, {})
        self.assertFalse(bot.close_all_requested)

    def test_close_all_keeps_position_when_market_closed(self):
        broker = StubBroker(market_is_open=False)
        bot = make_bot(broker)
        symbol = next(iter(config.INSTRUMENTS))
        bot.portfolio.positions[symbol] = __import__(
            "bot.models", fromlist=["PositionState"]).PositionState(
            symbol=symbol, direction="long", qty=1.0, entry_price=100.0,
            entry_time="", strategy="test", atr=2.0, stop_price=98.0)
        bot.close_all_requested = True
        bot.process_close_all()
        self.assertIn(symbol, bot.portfolio.positions)

    def test_update_status_snapshot(self):
        bot = make_bot(StubBroker())
        symbol = next(iter(config.INSTRUMENTS))
        state = bot.portfolio.open_position(
            symbol, "long", 2.0, "test", atr=2.0,
            stop_price_fn=lambda fill: fill - 2.0)
        state.extra["last_price"] = 110.0
        bot.update_status()
        position = bot.status["positions"][0]
        self.assertEqual(position["upnl"], 20.0)
        self.assertEqual(position["last_price"], 110.0)
        self.assertGreater(bot.status["equity"], 0)
        self.assertEqual(len(bot.status["equity_history"]), 1)
        self.assertTrue(all(b["connected"] for b in bot.status["brokers"]))
        # StubBroker heet geen "kraken": de positie telt als aandelen-potje
        self.assertEqual(bot.status["sleeves"]["stocks"]["used"], 220.0)
        self.assertEqual(bot.status["sleeves"]["crypto"]["used"], 0.0)


class DashboardHttpTests(unittest.TestCase):
    def setUp(self):
        self.bot = make_bot(StubBroker())
        self.bot.update_status()
        self.dashboard = Dashboard(self.bot, self.bot.portfolio.trades_csv,
                                   "127.0.0.1", 0, access_code="geheim",
                                   daily_pnl_csv=self.bot.portfolio.daily_pnl_csv)
        self.port = self.dashboard.start()
        self.base = f"http://127.0.0.1:{self.port}"

    def tearDown(self):
        self.dashboard.stop()

    def _post(self, path, payload):
        req = urllib.request.Request(
            self.base + path, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as err:
            return err.code, json.loads(err.read())

    def test_page_and_state(self):
        with urllib.request.urlopen(self.base + "/") as resp:
            self.assertEqual(resp.status, 200)
            self.assertIn(b"Multi-bot", resp.read())
        with urllib.request.urlopen(self.base + "/api/state") as resp:
            state = json.loads(resp.read())
        self.assertIn("equity", state)
        self.assertIn("trades", state)
        self.assertIn("stats", state)
        self.assertIn("daily", state)
        self.assertIn("brokers", state)
        self.assertIn("crypto_universe", state)
        self.assertFalse(state["paused"])

    def test_stats_reflect_completed_trades(self):
        symbol = next(iter(config.INSTRUMENTS))
        self.bot.portfolio.open_position(symbol, "long", 2.0, "test", atr=2.0,
                                         stop_price_fn=lambda fill: fill - 2.0)
        self.bot.portfolio.close_position(symbol, "test-exit")  # pnl 0.00
        state = self.dashboard.state()
        self.assertEqual(state["stats"]["count"], 1)
        self.assertEqual(state["stats"]["total"], 0.0)
        self.assertEqual(len(state["trade_curve"]), 1)
        self.assertEqual(state["trade_curve"][0][1], 0.0)
        self.assertEqual(state["per_instrument"], [[symbol, 0.0]])

    def test_wrong_code_is_rejected(self):
        status, body = self._post("/api/pause", {"code": "fout"})
        self.assertEqual(status, 403)
        self.assertFalse(self.bot.paused)

    def test_pause_and_close_all_with_code(self):
        status, _ = self._post("/api/pause", {"code": "geheim"})
        self.assertEqual(status, 200)
        self.assertTrue(self.bot.paused)
        status, _ = self._post("/api/close_all", {"code": "geheim"})
        self.assertEqual(status, 200)
        self.assertTrue(self.bot.close_all_requested)
        self.assertTrue(self.bot.paused)


if __name__ == "__main__":
    unittest.main()
