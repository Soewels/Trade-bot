"""Tests voor de aparte potjes: CRYPTO_BUDGET en STOCKS_BUDGET."""

import unittest

import config
from bot.models import Bar, Signal
from bot.risk_manager import RiskManager
from tests.test_crypto_screener import KrakenStub
from tests.test_dashboard import make_bot


def bars_with_atr(close: float = 100.0, spread: float = 1.0, n: int = 30):
    return [Bar(ts=float(i * 3600), open=close, high=close + spread,
                low=close - spread, close=close, volume=1000.0)
            for i in range(n)]


class MaxNotionalTests(unittest.TestCase):
    def test_max_notional_caps_position(self):
        risk = RiskManager()
        unlimited = risk.position_size(equity=100_000, price=50, atr=5,
                                       buying_power=1_000_000, fractional=False)
        self.assertEqual(unlimited, 200)
        capped = risk.position_size(equity=100_000, price=50, atr=5,
                                    buying_power=1_000_000, fractional=False,
                                    max_notional=500.0)
        self.assertEqual(capped, 10)  # 500 / 50


class SleeveBudgetTests(unittest.TestCase):
    def setUp(self):
        self._orig = (config.CRYPTO_BUDGET, config.STOCKS_BUDGET,
                      config.CRYPTO_MAX_PER_POSITION,
                      config.STOCKS_MAX_PER_POSITION)
        self.broker = KrakenStub()
        self.bot = make_bot(self.broker)
        self.bot._register_crypto("ETH/EUR")
        self.bot._register_crypto("SOL/EUR")
        # open BTC-positie: 0.1 stuks @ 100 = 10 EUR blootstelling
        self.bot.portfolio.open_position(
            "BTC/EUR", "long", 0.1, "test", atr=2.0,
            stop_price_fn=lambda fill: fill - 2.0)

    def tearDown(self):
        (config.CRYPTO_BUDGET, config.STOCKS_BUDGET,
         config.CRYPTO_MAX_PER_POSITION,
         config.STOCKS_MAX_PER_POSITION) = self._orig

    def test_sleeve_exposure_counts_only_crypto(self):
        self.assertAlmostEqual(self.bot.sleeve_exposure(crypto=True), 10.0)
        self.assertAlmostEqual(self.bot.sleeve_exposure(crypto=False), 0.0)

    def test_entry_is_capped_by_remaining_budget(self):
        config.CRYPTO_BUDGET = 15.0  # 10 in gebruik -> 5 EUR ruimte
        self.bot.execute(self.bot._momentum(), "ETH/EUR",
                         Signal("long", "test"), bars_with_atr())
        state = self.bot.portfolio.positions["ETH/EUR"]
        self.assertAlmostEqual(state.qty, 0.05)  # 5 EUR / prijs 100

    def test_entry_is_skipped_when_budget_is_full(self):
        config.CRYPTO_BUDGET = 8.0   # al 10 in gebruik -> vol
        self.bot.execute(self.bot._momentum(), "SOL/EUR",
                         Signal("long", "test"), bars_with_atr())
        self.assertNotIn("SOL/EUR", self.bot.portfolio.positions)

    def test_no_budget_means_no_cap(self):
        config.CRYPTO_BUDGET = 0.0
        self.bot.execute(self.bot._momentum(), "ETH/EUR",
                         Signal("long", "test"), bars_with_atr())
        state = self.bot.portfolio.positions["ETH/EUR"]
        self.assertGreater(state.qty, 0.05)

    def test_per_position_cap_guarantees_spreading(self):
        # ruim budget, maar max 20 EUR per positie -> 0.2 stuks @ 100
        config.CRYPTO_BUDGET = 1000.0
        config.CRYPTO_MAX_PER_POSITION = 20.0
        self.bot.execute(self.bot._momentum(), "ETH/EUR",
                         Signal("long", "test"), bars_with_atr())
        self.assertAlmostEqual(self.bot.portfolio.positions["ETH/EUR"].qty, 0.2)
        # en er blijft dus ruimte over voor een tweede munt
        self.bot.execute(self.bot._momentum(), "SOL/EUR",
                         Signal("long", "test"), bars_with_atr())
        self.assertAlmostEqual(self.bot.portfolio.positions["SOL/EUR"].qty, 0.2)

    def test_per_position_cap_works_without_budget(self):
        config.CRYPTO_BUDGET = 0.0
        config.CRYPTO_MAX_PER_POSITION = 30.0
        self.bot.execute(self.bot._momentum(), "ETH/EUR",
                         Signal("long", "test"), bars_with_atr())
        self.assertAlmostEqual(self.bot.portfolio.positions["ETH/EUR"].qty, 0.3)


if __name__ == "__main__":
    unittest.main()
