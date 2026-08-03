"""Unit tests for the broker layer: EU config wiring, exchange hours and
the Kraken paper mode. No network or broker SDK required."""

import os
import tempfile
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

import config
from bot.brokers import kraken_broker
from bot.brokers.base import BrokerError
from bot.brokers.ibkr_broker import exchange_is_open
from bot.brokers.kraken_broker import KrakenBroker
from bot.risk_manager import RiskManager


class ConfigProfileTests(unittest.TestCase):
    def test_eu_profile_is_consistent(self):
        market = config.MARKETS["eu"]
        strategy_symbols = (set(market["mean_reversion_symbols"])
                            | set(market["momentum_symbols"])
                            | set(market["trend_symbols"]))
        self.assertEqual(strategy_symbols, set(market["instruments"]))
        for sym in market["risk_on_pair"]:
            self.assertIn(sym, market["instruments"])
        for sym in market["correlation_blocked_symbols"]:
            self.assertIn(sym, market["instruments"])
        # EU instruments: not on Alpaca; EUR of USD (USD wordt live naar EUR
        # omgerekend), nooit GBP (pence-notering verstoort de sizing 100x)
        for sym, meta in market["instruments"].items():
            self.assertNotEqual(meta["broker"], "alpaca")
            if meta["broker"] == "ibkr":
                self.assertIn(meta["currency"], ("EUR", "USD"))

    def test_us_profile_is_consistent(self):
        market = config.MARKETS["us"]
        strategy_symbols = (set(market["mean_reversion_symbols"])
                            | set(market["momentum_symbols"])
                            | set(market["trend_symbols"]))
        self.assertEqual(strategy_symbols, set(market["instruments"]))


class ExchangeHoursTests(unittest.TestCase):
    def test_xetra_open_during_trading_day(self):
        tuesday_noon = datetime(2026, 7, 14, 12, 0, tzinfo=ZoneInfo("Europe/Berlin"))
        self.assertTrue(exchange_is_open("IBIS", tuesday_noon))

    def test_xetra_closed_in_evening_and_weekend(self):
        tuesday_evening = datetime(2026, 7, 14, 18, 0,
                                   tzinfo=ZoneInfo("Europe/Berlin"))
        self.assertFalse(exchange_is_open("IBIS", tuesday_evening))
        saturday = datetime(2026, 7, 18, 12, 0, tzinfo=ZoneInfo("Europe/Berlin"))
        self.assertFalse(exchange_is_open("IBIS", saturday))

    def test_timezone_conversion(self):
        # 08:30 UTC in summer = 10:30 in Berlin -> Xetra is open.
        morning_utc = datetime(2026, 7, 14, 8, 30, tzinfo=ZoneInfo("UTC"))
        self.assertTrue(exchange_is_open("IBIS", morning_utc))


class KrakenPaperModeTests(unittest.TestCase):
    def setUp(self):
        self._orig_price = kraken_broker.kraken.fetch_price
        kraken_broker.kraken.fetch_price = lambda pair, timeout=10: 50_000.0
        fd, self.state_path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.unlink(self.state_path)
        self.broker = KrakenBroker({"BTC/EUR": "XBTEUR"}, paper_cash=10_000.0,
                                   paper_state_file=self.state_path)

    def tearDown(self):
        kraken_broker.kraken.fetch_price = self._orig_price
        if os.path.exists(self.state_path):
            os.unlink(self.state_path)

    def test_starts_in_paper_mode_without_keys(self):
        self.assertTrue(self.broker.is_paper)
        self.assertEqual(self.broker.buying_power("BTC/EUR"), 10_000.0)
        self.assertTrue(self.broker.market_open("BTC/EUR"))
        self.assertFalse(self.broker.supports_short("BTC/EUR"))
        self.assertTrue(self.broker.allows_fractional("BTC/EUR"))

    def test_paper_buy_and_sell_round_trip(self):
        fill = self.broker.submit_market_order("BTC/EUR", "buy", 0.1)
        self.assertEqual(fill.price, 50_000.0)
        self.assertAlmostEqual(self.broker.paper["cash"], 5_000.0)
        self.assertAlmostEqual(self.broker.equity(), 10_000.0)
        self.assertEqual(self.broker.position_symbols(), {"BTC/EUR"})
        fill = self.broker.submit_market_order("BTC/EUR", "sell", 0.1)
        self.assertAlmostEqual(self.broker.paper["cash"], 10_000.0)
        self.assertEqual(self.broker.position_symbols(), set())

    def test_paper_buy_rejects_overspending(self):
        with self.assertRaises(BrokerError):
            self.broker.submit_market_order("BTC/EUR", "buy", 1.0)  # €50k > €10k

    def test_paper_state_survives_restart(self):
        self.broker.submit_market_order("BTC/EUR", "buy", 0.05)
        reloaded = KrakenBroker({"BTC/EUR": "XBTEUR"}, paper_cash=10_000.0,
                                paper_state_file=self.state_path)
        self.assertAlmostEqual(reloaded.paper["cash"], 7_500.0)
        self.assertAlmostEqual(reloaded.paper["qty"]["BTC/EUR"], 0.05)


class CryptoConfigTests(unittest.TestCase):
    def test_kraken_pair_uses_kraken_aliases(self):
        self.assertEqual(config.kraken_pair("BTC/EUR"), "XBTEUR")
        self.assertEqual(config.kraken_pair("DOGE/EUR"), "XDGEUR")
        self.assertEqual(config.kraken_pair("ETH/EUR"), "ETHEUR")
        self.assertEqual(config.kraken_pair("PEPE/EUR"), "PEPEEUR")

    def test_parse_crypto_symbols(self):
        self.assertEqual(
            config.parse_crypto_symbols("btc/eur, eth/eur ,BTC/EUR"),
            ["BTC/EUR", "ETH/EUR"])  # genormaliseerd en ontdubbeld
        with self.assertRaises(ValueError):
            config.parse_crypto_symbols("DOGE")  # geen quote-valuta
        with self.assertRaises(ValueError):
            config.parse_crypto_symbols(" , ")

    def test_connect_validates_pairs(self):
        orig = kraken_broker.kraken.fetch_price

        def only_btc(pair, timeout=10):
            if pair != "XBTEUR":
                raise Exception("Unknown asset pair")
            return 50_000.0

        kraken_broker.kraken.fetch_price = only_btc
        try:
            good = KrakenBroker({"BTC/EUR": "XBTEUR"},
                                paper_state_file="/tmp/nonexistent-kp.json")
            good.connect()  # mag niet gooien
            bad = KrakenBroker({"NEP/EUR": "NEPEUR"},
                               paper_state_file="/tmp/nonexistent-kp.json")
            with self.assertRaises(BrokerError):
                bad.connect()
        finally:
            kraken_broker.kraken.fetch_price = orig


class RiskOnPairTests(unittest.TestCase):
    def test_correlation_filter_uses_configured_pair(self):
        risk = RiskManager(risk_on_pair=("SXR8", "SXRV"))
        self.assertTrue(risk.correlation_blocks_crypto_long(
            {"SXR8": "long", "SXRV": "long"}))
        self.assertFalse(risk.correlation_blocks_crypto_long(
            {"SPY": "long", "QQQ": "long"}))  # US symbols are irrelevant now


if __name__ == "__main__":
    unittest.main()
