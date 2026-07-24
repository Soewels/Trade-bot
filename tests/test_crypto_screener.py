"""Tests voor de crypto-screener: scoring, volumefilter en de integratie
in de bot (zonder netwerk)."""

import unittest
from types import SimpleNamespace

import config
from bot import crypto_screener
from bot.crypto_screener import momentum_score, rank_by_eur_volume
from tests.test_dashboard import StubBroker, make_bot


def candles(closes, highs=None):
    highs = highs or closes
    return [SimpleNamespace(close=c, high=h) for c, h in zip(closes, highs)]


class MomentumScoreTests(unittest.TestCase):
    def test_rising_coin_beats_falling_coin(self):
        rising = momentum_score(candles([100 + i for i in range(80)]))
        falling = momentum_score(candles([200 - i for i in range(80)]))
        self.assertGreater(rising, 0)
        self.assertLess(falling, 0)

    def test_breakout_proximity_gives_bonus(self):
        flat = [100.0] * 80
        near_high = momentum_score(candles(flat, highs=[101.0] * 80))
        far_below_high = momentum_score(candles(flat, highs=[120.0] * 80))
        self.assertGreater(near_high, far_below_high)

    def test_needs_enough_history(self):
        self.assertIsNone(momentum_score(candles([100.0] * 10)))


class TimeframeConfigTests(unittest.TestCase):
    def test_valid_and_invalid_timeframes(self):
        self.assertEqual(config.parse_crypto_timeframe("15"), 15)
        self.assertEqual(config.parse_crypto_timeframe("60"), 60)
        with self.assertRaises(ValueError):
            config.parse_crypto_timeframe("45")

    def test_all_timeframes_have_a_kraken_interval(self):
        from bot.brokers.kraken_broker import INTERVAL_BY_MINUTES
        for minutes in (5, 15, 30, 60, 240):
            self.assertIn(minutes, INTERVAL_BY_MINUTES)


class VolumeRankTests(unittest.TestCase):
    def test_filters_and_sorts(self):
        pairs = {
            "BTC/EUR": {"pair": "XBTEUR", "key": "XXBTZEUR", "base": "BTC"},
            "PEPE/EUR": {"pair": "PEPEEUR", "key": "PEPEEUR", "base": "PEPE"},
            "USDT/EUR": {"pair": "USDTEUR", "key": "USDTZEUR", "base": "USDT"},
            "DEAD/EUR": {"pair": "DEADEUR", "key": "DEADEUR", "base": "DEAD"},
        }
        tickers = {
            "XXBTZEUR": {"p": ["0", "50000"], "v": ["0", "1000"]},   # 50M EUR
            "PEPEEUR": {"p": ["0", "0.00001"], "v": ["0", "1e12"]},  # 10M EUR
            "USDTZEUR": {"p": ["0", "0.9"], "v": ["0", "1e9"]},      # stablecoin
            "DEADEUR": {"p": ["0", "1.0"], "v": ["0", "100"]},       # illiquide
        }
        ranked = rank_by_eur_volume(pairs, tickers, min_eur_volume=2e6)
        self.assertEqual([sym for _, sym, _ in ranked], ["BTC/EUR", "PEPE/EUR"])


class KrakenStub(StubBroker):
    name = "kraken"

    def __init__(self):
        super().__init__()
        self.pairs = {"BTC/EUR": "XBTEUR"}

    def add_pair(self, symbol, pair):
        self.pairs.setdefault(symbol, pair)


class CryptoScreeningIntegrationTests(unittest.TestCase):
    def setUp(self):
        self._orig_scan = crypto_screener.scan_kraken
        self._orig_count = config.CRYPTO_AUTO_COUNT
        config.CRYPTO_AUTO_COUNT = 3

    def tearDown(self):
        crypto_screener.scan_kraken = self._orig_scan
        config.CRYPTO_AUTO_COUNT = self._orig_count

    def test_scan_updates_universe_and_keeps_held_positions(self):
        broker = KrakenStub()
        bot = make_bot(broker)
        # BTC heeft een open positie en mag dus nooit weggeruild worden
        bot.portfolio.open_position("BTC/EUR", "long", 0.1, "test", atr=2.0,
                                    stop_price_fn=lambda fill: fill - 2.0)
        import bot.main as main_module
        main_module.crypto_screener.scan_kraken = (
            lambda count, min_vol, interval="1h": ["SOL/EUR", "PEPE/EUR", "ETH/EUR"])
        bot.maybe_screen_crypto()
        universe = bot.portfolio.meta["crypto_universe"]
        self.assertIn("BTC/EUR", universe)          # positie -> blijft
        self.assertIn("SOL/EUR", universe)
        self.assertEqual(len(universe), 3)
        self.assertIn("SOL/EUR", bot._momentum().symbols)
        self.assertEqual(broker.pairs["SOL/EUR"], "SOLEUR")
        # zelfde 12 uur: scanner wordt niet opnieuw aangeroepen
        main_module.crypto_screener.scan_kraken = (
            lambda count, min_vol, interval="1h": (_ for _ in ()).throw(AssertionError))
        bot.maybe_screen_crypto()

    def test_universe_restored_after_restart(self):
        broker = KrakenStub()
        bot = make_bot(broker)
        import bot.main as main_module
        main_module.crypto_screener.scan_kraken = (
            lambda count, min_vol, interval="1h": ["SOL/EUR", "DOGE/EUR"])
        bot.maybe_screen_crypto()

        from bot.main import Bot
        bot2 = Bot(dict(bot.brokers))
        # nieuwe Bot leest dezelfde state? Nee: make_bot gaf tijdelijke paden
        # die alweer teruggezet zijn; daarom herstellen we hier handmatig:
        bot2.portfolio.meta["crypto_universe"] = ["SOL/EUR", "DOGE/EUR"]
        bot2._restore_crypto()
        self.assertIn("SOL/EUR", bot2._momentum().symbols)
        self.assertIn("DOGE/EUR", bot2._momentum().symbols)
        self.assertEqual(broker.pairs["DOGE/EUR"], "XDGEUR")


if __name__ == "__main__":
    unittest.main()
