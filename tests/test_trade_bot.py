"""Tests voor indicatoren, strategieën, portfolio en backtester."""

import math
import unittest
from datetime import datetime, timedelta, timezone

from trade_bot.backtest import run_backtest
from trade_bot.config import BotConfig
from trade_bot.data import Candle
from trade_bot.indicators import ema, macd, rsi, sma
from trade_bot.portfolio import Portfolio
from trade_bot.strategy import MacdStrategy, RsiStrategy, Signal, SmaCrossStrategy


def make_candles(closes: list[float]) -> list[Candle]:
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    return [
        Candle(
            open_time=start + timedelta(hours=i),
            open=c, high=c * 1.005, low=c * 0.995, close=c, volume=1.0,
        )
        for i, c in enumerate(closes)
    ]


class TestIndicators(unittest.TestCase):
    def test_sma_basic(self):
        result = sma([1, 2, 3, 4, 5], 3)
        self.assertEqual(result[:2], [None, None])
        self.assertEqual(result[2:], [2.0, 3.0, 4.0])

    def test_ema_seeded_with_sma(self):
        result = ema([2, 4, 6, 8], 2)
        self.assertIsNone(result[0])
        self.assertAlmostEqual(result[1], 3.0)

    def test_rsi_all_gains_is_100(self):
        prices = list(range(1, 20))
        values = rsi([float(p) for p in prices], 14)
        self.assertEqual(values[-1], 100.0)

    def test_macd_histogram_is_difference(self):
        prices = [100 + 5 * math.sin(i / 5) + 0.1 * i for i in range(80)]
        macd_line, signal_line, histogram = macd(prices, 12, 26, 9)
        self.assertEqual(len(macd_line), len(prices))
        for m, s, h in zip(macd_line, signal_line, histogram):
            if h is not None:
                self.assertAlmostEqual(h, m - s)

    def test_macd_rejects_invalid_periods(self):
        with self.assertRaises(ValueError):
            macd([1.0] * 50, 26, 12, 9)

    def test_rsi_bounds(self):
        prices = [100 + 5 * math.sin(i / 3) for i in range(60)]
        for v in rsi(prices, 14):
            if v is not None:
                self.assertTrue(0 <= v <= 100)


class TestStrategies(unittest.TestCase):
    def test_sma_cross_buy_signal(self):
        # dalende prijzen gevolgd door sterke stijging → snelle SMA kruist omhoog
        closes = [100.0 - i for i in range(30)] + [80.0 + 3 * i for i in range(15)]
        strategy = SmaCrossStrategy(5, 20)
        signals = [strategy.signal(closes[: i + 1]) for i in range(len(closes))]
        self.assertIn(Signal.BUY, signals)

    def test_sma_cross_sell_signal(self):
        closes = [100.0 + i for i in range(30)] + [130.0 - 3 * i for i in range(15)]
        strategy = SmaCrossStrategy(5, 20)
        signals = [strategy.signal(closes[: i + 1]) for i in range(len(closes))]
        self.assertIn(Signal.SELL, signals)

    def test_sma_cross_holds_without_data(self):
        self.assertIs(SmaCrossStrategy(5, 20).signal([1.0, 2.0]), Signal.HOLD)

    def test_rsi_strategy_buys_on_oversold_recovery(self):
        closes = [100.0 - 2 * i for i in range(20)] + [60.0 + 4 * i for i in range(10)]
        strategy = RsiStrategy(14, 30, 70)
        signals = [strategy.signal(closes[: i + 1]) for i in range(len(closes))]
        self.assertIn(Signal.BUY, signals)

    def test_macd_strategy_generates_signals(self):
        # golvende markt → MACD kruist de signaallijn in beide richtingen
        closes = [100.0 + 20.0 * math.sin(i / 15.0) for i in range(150)]
        strategy = MacdStrategy(12, 26, 9)
        signals = [strategy.signal(closes[: i + 1]) for i in range(len(closes))]
        self.assertIn(Signal.BUY, signals)
        self.assertIn(Signal.SELL, signals)

    def test_macd_strategy_holds_without_data(self):
        self.assertIs(MacdStrategy().signal([1.0] * 10), Signal.HOLD)

    def test_invalid_periods_rejected(self):
        with self.assertRaises(ValueError):
            SmaCrossStrategy(20, 10)
        with self.assertRaises(ValueError):
            RsiStrategy(14, 70, 30)
        with self.assertRaises(ValueError):
            MacdStrategy(26, 12, 9)


class TestPortfolio(unittest.TestCase):
    def test_buy_and_sell_roundtrip_with_fees(self):
        p = Portfolio(cash=1000.0, fee_rate=0.001)
        p.buy(price=100.0, cash_fraction=1.0)
        self.assertTrue(p.in_position)
        self.assertAlmostEqual(p.cash, 0.0)
        self.assertAlmostEqual(p.position, 999.0 / 100.0)

        p.sell(price=110.0)
        self.assertFalse(p.in_position)
        self.assertGreater(p.cash, 1000.0)  # winst ondanks fees

    def test_no_double_buy(self):
        p = Portfolio(cash=1000.0)
        self.assertIsNotNone(p.buy(100.0, 0.5))
        self.assertIsNone(p.buy(100.0, 0.5))

    def test_sell_without_position_is_noop(self):
        p = Portfolio(cash=1000.0)
        self.assertIsNone(p.sell(100.0))

    def test_stop_loss_and_take_profit_detection(self):
        p = Portfolio(cash=1000.0)
        p.buy(price=100.0, cash_fraction=1.0)
        self.assertEqual(p.check_risk(94.0, stop_loss=0.05, take_profit=0.15), "stop_loss")
        self.assertEqual(p.check_risk(116.0, stop_loss=0.05, take_profit=0.15), "take_profit")
        self.assertIsNone(p.check_risk(101.0, stop_loss=0.05, take_profit=0.15))


class TestBacktest(unittest.TestCase):
    def test_backtest_runs_and_reports(self):
        closes = [100.0 - i for i in range(30)] + [70.0 + 2 * i for i in range(40)] \
            + [150.0 - 2 * i for i in range(20)]
        config = BotConfig(strategy="sma_cross", fast_period=5, slow_period=20,
                           stop_loss=0.0, take_profit=0.0)
        result = run_backtest(make_candles(closes), config)
        self.assertGreater(result.num_trades, 0)
        self.assertAlmostEqual(
            result.total_return_pct,
            (result.final_equity - result.start_cash) / result.start_cash * 100.0,
        )
        self.assertGreaterEqual(result.max_drawdown_pct, 0.0)

    def test_backtest_equity_consistent_without_trades(self):
        # vlakke markt → geen kruisingen → geen trades, equity blijft startkapitaal
        closes = [100.0] * 60
        config = BotConfig(strategy="sma_cross", fast_period=5, slow_period=20)
        result = run_backtest(make_candles(closes), config)
        self.assertEqual(result.num_trades, 0)
        self.assertAlmostEqual(result.final_equity, config.start_cash)

    def test_backtest_requires_data(self):
        with self.assertRaises(ValueError):
            run_backtest([], BotConfig())


if __name__ == "__main__":
    unittest.main()
