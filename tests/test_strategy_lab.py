"""Tests voor het strategie-lab: mini-backtester, toernooi, wisselregels
en het herstellen van de gekozen kampioen na een herstart."""

import math
import os
import tempfile
import unittest

import config
from bot import strategy_lab
from bot.backtester import WARMUP_BARS, simulate
from bot.models import Bar
from bot.strategies.candidates import (BollingerDipStrategy, EmaCrossStrategy,
                                       RsiDipStrategy)
from bot.strategies.momentum_breakout import MomentumBreakoutStrategy


def make_bars(closes: list[float], spread: float = 0.5,
              volume: float = 100.0) -> list[Bar]:
    bars = []
    for i, close in enumerate(closes):
        bars.append(Bar(ts=i * 3600.0, open=close, high=close + spread,
                        low=close - spread, close=close, volume=volume))
    return bars


def wavy_series(n: int, base: float = 100.0, amplitude: float = 8.0,
                period: float = 40.0) -> list[float]:
    """Zijwaartse markt met duidelijke dips en toppen (voor dip-kopers)."""
    return [base + amplitude * math.sin(2 * math.pi * i / period)
            for i in range(n)]


def trending_series(n: int, base: float = 100.0, step: float = 0.4,
                    amplitude: float = 1.5, period: float = 20.0) -> list[float]:
    """Gestaag stijgende markt met wat ruis (voor trendvolgers)."""
    return [base + step * i + amplitude * math.sin(2 * math.pi * i / period)
            for i in range(n)]


class BacktesterTests(unittest.TestCase):
    def test_ema_cross_profits_in_uptrend(self):
        # eerst dalen (na de warm-up), dan een lange stijging: de kruising
        # omhoog gebeurt in de geteste periode en levert winst op
        closes = ([120.0 - 0.2 * i for i in range(100)]
                  + [100.0 + 0.5 * i for i in range(200)])
        bars = make_bars(closes)
        strategy = EmaCrossStrategy(["X/EUR"], timeframe_minutes=60)
        result = simulate(strategy, "X/EUR", bars)
        self.assertGreater(result.trades, 0)
        self.assertGreater(result.total_return, 0)
        self.assertGreaterEqual(result.max_drawdown, 0)

    def test_flat_market_produces_no_trades_for_breakout(self):
        bars = make_bars([100.0] * 300)
        strategy = MomentumBreakoutStrategy(["X/EUR"], timeframe_minutes=60)
        result = simulate(strategy, "X/EUR", bars)
        self.assertEqual(result.trades, 0)
        self.assertEqual(result.total_return, 0.0)

    def test_score_penalises_drawdown(self):
        from bot.backtester import BacktestResult
        calm = BacktestResult(total_return=0.05, max_drawdown=0.01, trades=5)
        wild = BacktestResult(total_return=0.06, max_drawdown=0.08, trades=5)
        self.assertGreater(calm.score, wild.score)

    def test_stop_loss_limits_loss_to_about_one_percent(self):
        # sterke stijging (zet de entry op) gevolgd door een crash
        closes = trending_series(120, step=0.8) + [30.0] * 30
        bars = make_bars(closes, volume=100.0)
        bars[119].volume = 1000.0  # volume-spike voor de breakout-entry
        strategy = MomentumBreakoutStrategy(["X/EUR"], timeframe_minutes=60,
                                            trail_atr_mult=None)
        result = simulate(strategy, "X/EUR", bars)
        if result.trades:  # verlies blijft in de buurt van 1% + kosten
            self.assertGreater(result.total_return, -0.03)


class CandidateStrategyTests(unittest.TestCase):
    def test_rsi_dip_buys_recovery_and_exits(self):
        strategy = RsiDipStrategy(["X/EUR"])
        # scherpe daling (oversold), daarna krachtig herstel: ergens tijdens
        # het herstel moet er precies één long-kruising en daarna een exit zijn
        closes = ([100.0 - 2 * i for i in range(20)]
                  + [60.0 + 3 * i for i in range(1, 20)])
        bars = make_bars(closes)
        actions = []
        side = None
        for i in range(15, len(bars)):
            signal = strategy.evaluate("X/EUR", bars[:i + 1], side)
            if signal:
                actions.append(signal.action)
                side = "long" if signal.action == "long" else None
        self.assertEqual(actions, ["long", "exit"])

    def test_bollinger_dip_buys_below_band(self):
        strategy = BollingerDipStrategy(["X/EUR"])
        closes = [100.0 + (i % 2) * 0.5 for i in range(25)] + [90.0]
        signal = strategy.evaluate("X/EUR", make_bars(closes), None)
        self.assertIsNotNone(signal)
        self.assertEqual(signal.action, "long")
        back = closes + [101.0]
        exit_sig = strategy.evaluate("X/EUR", make_bars(back), "long")
        self.assertIsNotNone(exit_sig)
        self.assertEqual(exit_sig.action, "exit")

    def test_ema_cross_signals(self):
        strategy = EmaCrossStrategy(["X/EUR"], fast=3, slow=6)
        # vlak, dan één stijgende candle: de snelle EMA kruist nu pas omhoog
        closes = [100.0] * 20 + [101.0]
        signal = strategy.evaluate("X/EUR", make_bars(closes), None)
        self.assertIsNotNone(signal)
        self.assertEqual(signal.action, "long")
        down = [100.0] * 20 + [101.0, 95.0]
        exit_sig = strategy.evaluate("X/EUR", make_bars(down), "long")
        self.assertIsNotNone(exit_sig)
        self.assertEqual(exit_sig.action, "exit")

    def test_every_candidate_can_be_built(self):
        for name in strategy_lab.CANDIDATES:
            strategy = strategy_lab.build_candidate(name, ["BTC/EUR"], 15)
            self.assertEqual(strategy.name, name)
            self.assertEqual(strategy.symbols, ["BTC/EUR"])
            self.assertEqual(strategy.timeframe_minutes, 15)
            self.assertIn(name, strategy_lab.LABELS_NL)


class TournamentTests(unittest.TestCase):
    def test_tournament_scores_all_candidates_on_both_halves(self):
        bars = make_bars(trending_series(2 * strategy_lab.MIN_HALF_BARS + 20))
        results = strategy_lab.run_tournament({"BTC/EUR": bars}, 60)
        self.assertEqual(set(results), set(strategy_lab.CANDIDATES))
        for res in results.values():
            self.assertAlmostEqual(res["score"], res["half1"] + res["half2"])
            self.assertGreaterEqual(res["trades"], 0)

    def test_tournament_skips_short_history(self):
        bars = make_bars(trending_series(WARMUP_BARS))  # veel te kort
        self.assertEqual(strategy_lab.run_tournament({"BTC/EUR": bars}, 60), {})


class ChooseTests(unittest.TestCase):
    CHAMP = {"half1": 0.01, "half2": 0.01, "score": 0.02, "trades": 10}

    def test_keeps_champion_when_no_challenger_qualifies(self):
        results = {"momentum_breakout": self.CHAMP,
                   "rsi_dip": {"half1": 0.005, "half2": 0.03,
                               "score": 0.035, "trades": 10}}  # helft 1 slechter
        self.assertEqual(
            strategy_lab.choose("momentum_breakout", results, 0.25, 4),
            "momentum_breakout")

    def test_switches_when_challenger_wins_both_halves_with_margin(self):
        results = {"momentum_breakout": self.CHAMP,
                   "rsi_dip": {"half1": 0.03, "half2": 0.03,
                               "score": 0.06, "trades": 10}}
        self.assertEqual(
            strategy_lab.choose("momentum_breakout", results, 0.25, 4),
            "rsi_dip")

    def test_small_improvement_is_not_enough(self):
        results = {"momentum_breakout": self.CHAMP,
                   "rsi_dip": {"half1": 0.011, "half2": 0.011,
                               "score": 0.022, "trades": 10}}  # maar +10%
        self.assertEqual(
            strategy_lab.choose("momentum_breakout", results, 0.25, 4),
            "momentum_breakout")

    def test_too_few_trades_disqualifies_challenger(self):
        results = {"momentum_breakout": self.CHAMP,
                   "rsi_dip": {"half1": 0.05, "half2": 0.05,
                               "score": 0.10, "trades": 2}}
        self.assertEqual(
            strategy_lab.choose("momentum_breakout", results, 0.25, 4),
            "momentum_breakout")

    def test_best_of_multiple_qualifying_challengers_wins(self):
        results = {"momentum_breakout": self.CHAMP,
                   "rsi_dip": {"half1": 0.03, "half2": 0.03,
                               "score": 0.06, "trades": 10},
                   "ema_cross": {"half1": 0.05, "half2": 0.05,
                                 "score": 0.10, "trades": 10}}
        self.assertEqual(
            strategy_lab.choose("momentum_breakout", results, 0.25, 4),
            "ema_cross")

    def test_missing_champion_keeps_champion(self):
        self.assertEqual(strategy_lab.choose("momentum_breakout", {}, 0.25, 4),
                         "momentum_breakout")


class BotIntegrationTests(unittest.TestCase):
    """Wissel + persistentie via de echte Bot-klasse (stub-brokers)."""

    def _make_bot(self):
        from tests.test_dashboard import StubBroker, make_bot
        return make_bot(StubBroker()), StubBroker

    def test_switch_replaces_strategy_and_persists(self):
        bot, _ = self._make_bot()
        old = bot.crypto_strategy
        symbols_before = list(old.symbols)
        bot._switch_crypto_strategy("rsi_dip")
        self.assertEqual(bot.crypto_strategy.name, "rsi_dip")
        self.assertEqual(bot.crypto_strategy.symbols, symbols_before)
        self.assertNotIn(old, bot.strategies)
        self.assertIn(bot.crypto_strategy, bot.strategies)
        self.assertEqual(bot.portfolio.meta["crypto_strategy"], "rsi_dip")

    def test_restore_rebuilds_saved_champion(self):
        bot, StubBroker = self._make_bot()
        bot._switch_crypto_strategy("ema_cross")
        bot.portfolio.save()
        state_file = bot.portfolio.state_file

        originals = {"STATE_FILE": config.STATE_FILE}
        config.STATE_FILE = state_file
        try:
            from bot.main import Bot
            broker = StubBroker()
            reborn = Bot({sym: broker for sym in config.INSTRUMENTS})
            self.assertEqual(reborn.crypto_strategy.name, "ema_cross")
            self.assertIn(reborn.crypto_strategy, reborn.strategies)
        finally:
            config.STATE_FILE = originals["STATE_FILE"]

    def test_unknown_saved_name_falls_back_to_default(self):
        bot, _ = self._make_bot()
        bot.portfolio.meta["crypto_strategy"] = "bestaat_niet"
        bot._restore_crypto_strategy()
        self.assertEqual(bot.crypto_strategy.name, "momentum_breakout")


if __name__ == "__main__":
    unittest.main()
