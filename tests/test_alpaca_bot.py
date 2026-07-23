"""Unit tests for the Alpaca bot: indicators, strategies and risk manager.

These modules are pure Python, so no API access or alpaca-trade-api install
is needed to run them.
"""

import unittest

from bot.indicators import atr_last, atr_series, ema_series, sma_last, stdev_last
from bot.models import Bar, PositionState
from bot.risk_manager import RiskManager
from bot.strategies import (MeanReversionStrategy, MomentumBreakoutStrategy,
                            TrendFollowingStrategy)


def make_bars(closes, volumes=None, highs=None, lows=None):
    volumes = volumes or [1000.0] * len(closes)
    highs = highs or closes
    lows = lows or closes
    return [Bar(ts=float(i * 60), open=c, high=h, low=l, close=c, volume=v)
            for i, (c, h, l, v) in enumerate(zip(closes, highs, lows, volumes))]


class IndicatorTests(unittest.TestCase):
    def test_sma_and_stdev(self):
        self.assertEqual(sma_last([1, 2, 3, 4], 2), 3.5)
        self.assertIsNone(sma_last([1, 2], 3))
        self.assertAlmostEqual(stdev_last([2, 2, 2, 2], 4), 0.0)
        self.assertAlmostEqual(stdev_last([1, 3], 2), 1.0)

    def test_ema_series(self):
        values = ema_series([1, 2, 3, 4, 5], 3)
        self.assertIsNone(values[1])
        self.assertAlmostEqual(values[2], 2.0)
        self.assertAlmostEqual(values[3], 3.0)
        self.assertAlmostEqual(values[4], 4.0)

    def test_atr_wilder(self):
        bars = [
            Bar(0, 9, 10, 8, 9, 0),
            Bar(1, 10, 11, 9, 10, 0),
            Bar(2, 11, 12, 10, 11, 0),
            Bar(3, 14, 15, 11, 14, 0),
        ]
        values = atr_series(bars, 3)
        self.assertIsNone(values[1])
        self.assertAlmostEqual(values[2], 2.0)
        self.assertAlmostEqual(values[3], (2.0 * 2 + 4.0) / 3)
        self.assertAlmostEqual(atr_last(bars, 3), (2.0 * 2 + 4.0) / 3)


class MeanReversionTests(unittest.TestCase):
    def setUp(self):
        self.strategy = MeanReversionStrategy({"SPY": 1.5, "QQQ": 1.8}, period=20)

    def test_long_below_band_and_short_above(self):
        bars = make_bars([100.0] * 24 + [90.0])
        signal = self.strategy.evaluate("SPY", bars, None)
        self.assertEqual(signal.action, "long")
        bars = make_bars([100.0] * 24 + [110.0])
        signal = self.strategy.evaluate("SPY", bars, None)
        self.assertEqual(signal.action, "short")

    def test_hold_inside_band(self):
        bars = make_bars([100.0, 101.0] * 12 + [100.5])
        self.assertIsNone(self.strategy.evaluate("SPY", bars, None))

    def test_qqq_uses_wider_threshold(self):
        # A move that is > 1.5 but < 1.8 standard deviations from the mean.
        closes = [100.0, 102.0] * 10 + [99.2]
        bars = make_bars(closes)
        self.assertEqual(self.strategy.evaluate("SPY", bars, None).action, "long")
        self.assertIsNone(self.strategy.evaluate("QQQ", bars, None))

    def test_exit_when_price_reverts_to_mean(self):
        bars = make_bars([100.0] * 24 + [101.0])
        signal = self.strategy.evaluate("SPY", bars, "long")
        self.assertEqual(signal.action, "exit")
        signal = self.strategy.evaluate("SPY", bars, "short")
        self.assertIsNone(signal)  # short has not reverted below the mean yet

    def test_no_signal_without_enough_bars(self):
        self.assertIsNone(self.strategy.evaluate("SPY", make_bars([100.0] * 10), None))


class MomentumBreakoutTests(unittest.TestCase):
    def setUp(self):
        self.strategy = MomentumBreakoutStrategy(["BTC/USD"], period=20,
                                                 volume_mult=1.5)

    def _channel_bars(self, last_close, last_volume):
        closes = [95.0] * 20 + [last_close]
        highs = [100.0] * 20 + [max(last_close, 100.0)]
        lows = [90.0] * 20 + [min(last_close, 90.0)]
        volumes = [1000.0] * 20 + [last_volume]
        return make_bars(closes, volumes=volumes, highs=highs, lows=lows)

    def test_breakout_with_volume_goes_long(self):
        bars = self._channel_bars(last_close=105.0, last_volume=2000.0)
        signal = self.strategy.evaluate("BTC/USD", bars, None)
        self.assertEqual(signal.action, "long")

    def test_breakout_without_volume_is_ignored(self):
        bars = self._channel_bars(last_close=105.0, last_volume=1200.0)
        self.assertIsNone(self.strategy.evaluate("BTC/USD", bars, None))

    def test_breakdown_signals_short(self):
        bars = self._channel_bars(last_close=85.0, last_volume=2000.0)
        signal = self.strategy.evaluate("BTC/USD", bars, "long")
        self.assertEqual(signal.action, "short")  # executor exits the long

    def test_no_signal_inside_channel(self):
        bars = self._channel_bars(last_close=95.0, last_volume=2000.0)
        self.assertIsNone(self.strategy.evaluate("BTC/USD", bars, None))


class TrendFollowingTests(unittest.TestCase):
    def test_golden_and_death_cross(self):
        strategy = TrendFollowingStrategy(["GLD"], fast_period=3, slow_period=5)
        # Falling then sharply rising: fast EMA crosses above slow EMA.
        closes = [100, 98, 96, 94, 92, 90, 110]
        signal = strategy.evaluate("GLD", make_bars([float(c) for c in closes]), None)
        self.assertEqual(signal.action, "long")
        # Rising then sharply falling: fast EMA crosses below slow EMA.
        closes = [100, 102, 104, 106, 108, 110, 90]
        signal = strategy.evaluate("GLD", make_bars([float(c) for c in closes]), "long")
        self.assertEqual(signal.action, "short")

    def test_requires_enough_history(self):
        strategy = TrendFollowingStrategy(["GLD"], fast_period=50, slow_period=200)
        self.assertIsNone(strategy.evaluate("GLD", make_bars([100.0] * 100), None))

    def test_rejects_fast_ge_slow(self):
        with self.assertRaises(ValueError):
            TrendFollowingStrategy(["GLD"], fast_period=200, slow_period=50)


class RiskManagerTests(unittest.TestCase):
    def setUp(self):
        self.risk = RiskManager(risk_per_trade=0.01, max_notional_fraction=0.95)

    def test_atr_position_sizing(self):
        # 1% of 100k equity = $1000 risk; ATR $5 -> 200 shares.
        qty = self.risk.position_size(equity=100_000, price=50, atr=5,
                                      buying_power=1_000_000, fractional=False)
        self.assertEqual(qty, 200)
        # A quieter instrument (smaller ATR) gets a larger position.
        quiet = self.risk.position_size(equity=100_000, price=50, atr=2,
                                        buying_power=1_000_000, fractional=False)
        self.assertGreater(quiet, qty)

    def test_size_capped_by_buying_power(self):
        qty = self.risk.position_size(equity=100_000, price=50, atr=5,
                                      buying_power=5_000, fractional=False)
        self.assertEqual(qty, 95)  # 5000 * 0.95 / 50

    def test_fractional_sizing_for_crypto(self):
        qty = self.risk.position_size(equity=10_000, price=60_000, atr=1_500,
                                      buying_power=100_000, fractional=True)
        self.assertAlmostEqual(qty, 0.066666, places=6)

    def test_zero_when_inputs_invalid(self):
        self.assertEqual(self.risk.position_size(0, 50, 5, 1000, False), 0.0)
        self.assertEqual(self.risk.position_size(1000, 50, 0, 1000, False), 0.0)

    def test_hard_stop_is_one_atr_from_entry(self):
        self.assertEqual(self.risk.hard_stop_price(100, 2, "long"), 98)
        self.assertEqual(self.risk.hard_stop_price(100, 2, "short"), 102)

    def test_trailing_stop_ratchets_up_only(self):
        state = PositionState(symbol="BTC/USD", direction="long", qty=1,
                              entry_price=100, entry_time="", strategy="test",
                              atr=2, stop_price=98, trail_atr_mult=2,
                              peak_price=100)
        self.risk.update_trailing_stop(state, 110)
        self.assertEqual(state.stop_price, 106)  # 110 - 2*2
        self.risk.update_trailing_stop(state, 104)
        self.assertEqual(state.stop_price, 106)  # never loosens
        self.assertTrue(self.risk.stop_hit(state, 105))
        self.assertFalse(self.risk.stop_hit(state, 107))

    def test_trailing_stop_for_short(self):
        state = PositionState(symbol="GLD", direction="short", qty=1,
                              entry_price=100, entry_time="", strategy="test",
                              atr=2, stop_price=102, trail_atr_mult=3,
                              peak_price=100)
        self.risk.update_trailing_stop(state, 90)
        self.assertEqual(state.stop_price, 96)  # 90 + 3*2
        self.assertTrue(self.risk.stop_hit(state, 97))

    def test_correlation_filter(self):
        blocks = self.risk.correlation_blocks_crypto_long
        self.assertTrue(blocks({"SPY": "long", "QQQ": "long"}))
        self.assertFalse(blocks({"SPY": "long", "QQQ": "short"}))
        self.assertFalse(blocks({"SPY": "long"}))
        self.assertFalse(blocks({}))


if __name__ == "__main__":
    unittest.main()
