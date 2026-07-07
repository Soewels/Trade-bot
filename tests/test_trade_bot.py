"""Tests voor indicatoren, strategieën, portfolio en backtester."""

import math
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from trade_bot.backtest import run_backtest
from trade_bot.bot import TradeBot, quote_asset
from trade_bot.config import BotConfig
from trade_bot.data import Candle
from trade_bot.exchange import BinanceExchange, ExchangeError, Fill
from trade_bot.kraken import KrakenExchange, parse_ohlc
from trade_bot.kraken import fetch_candles as kraken_fetch_candles
from trade_bot.market import Market, get_market
from trade_bot.notify import TelegramNotifier
from trade_bot.state import load_state, save_state
from trade_bot.webapp import Dashboard
from trade_bot.indicators import ema, macd, rsi, sma
from trade_bot.portfolio import Portfolio
from trade_bot.strategy import MacdStrategy, RsiStrategy, Signal, SmaCrossStrategy


def patch_market(bot, candles):
    """Vervang de marktdata van een bot door vaste candles (geen netwerk)."""
    fake = Market("test", lambda *a, **k: candles, lambda *a, **k: candles[-1].close)
    return mock.patch.object(bot, "market", fake)


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


class FakeExchange:
    """Nep-exchange voor tests: registreert orders, raakt geen netwerk aan."""

    testnet = True

    def __init__(self, balance: float = 1000.0, price: float = 100.0):
        self.balance = balance
        self.price = price
        self.orders: list[tuple[str, float]] = []

    def free_balance(self, asset: str) -> float:
        return self.balance

    def symbol_filters(self, symbol: str) -> dict:
        return {"step_size": "0.0001", "min_qty": "0.0001", "min_notional": "5"}

    def market_buy(self, symbol: str, quote_amount: float) -> Fill:
        self.orders.append(("BUY", quote_amount))
        return Fill(symbol, "BUY", self.price, quote_amount / self.price, quote_amount)

    def market_sell(self, symbol: str, quantity: float) -> Fill:
        self.orders.append(("SELL", quantity))
        return Fill(symbol, "SELL", self.price, quantity, quantity * self.price)


class TestExchange(unittest.TestCase):
    def test_signature_matches_binance_docs_example(self):
        # bekend voorbeeld uit de officiële Binance API-documentatie
        exchange = BinanceExchange(
            api_key="vmPUZE6mv9SD5VNHk4HlWFsOr6aKE2zvsw0MuIgwCIPy6utIco14y7Ju91duEh8A",
            api_secret="NhqPtmdSJYdKjVHjA7PZj4Mge3R5YNiP1e3UZjInClVN65XAbvqqM6A7H5fATj0j",
        )
        query = ("symbol=LTCBTC&side=BUY&type=LIMIT&timeInForce=GTC&quantity=1"
                 "&price=0.1&recvWindow=5000&timestamp=1499827319559")
        self.assertEqual(
            exchange.sign(query),
            "c8db56825ae71d6d79447849e617115f4a920fa2acdcab2b053c4b2838bd6b71",
        )

    def test_missing_keys_rejected(self):
        with self.assertRaises(ValueError):
            BinanceExchange(api_key="", api_secret="")

    def test_round_quantity_floors_to_step(self):
        exchange = BinanceExchange(api_key="k", api_secret="s")
        exchange._filters["BTCUSDT"] = {"step_size": "0.001", "min_qty": "0.001",
                                        "min_notional": "5"}
        self.assertAlmostEqual(exchange.round_quantity("BTCUSDT", 0.123456), 0.123)
        self.assertAlmostEqual(exchange.round_quantity("BTCUSDT", 0.0009), 0.0)

    def test_fill_from_order_rejects_empty_execution(self):
        with self.assertRaises(ExchangeError):
            BinanceExchange._fill_from_order(
                {"symbol": "BTCUSDT", "executedQty": "0", "cummulativeQuoteQty": "0"}, "BUY")

    def test_quote_asset_detection(self):
        self.assertEqual(quote_asset("BTCUSDT"), "USDT")
        self.assertEqual(quote_asset("ethbtc"), "BTC")
        with self.assertRaises(ValueError):
            quote_asset("BTCXYZQ")


class TestKraken(unittest.TestCase):
    # officieel voorbeeld uit https://docs.kraken.com/rest/#section/Authentication
    DOC_SECRET = ("kQH5HW/8p1uGOVjbgWA7FunAmGO8lsSUXNsu3eow76sz84Q18fWxnyRzBHCd3pd5"
                  "nE9qa99HAZtuZuj6F1huXg==")

    def test_signature_matches_kraken_docs_example(self):
        exchange = KrakenExchange(api_key="key", api_secret=self.DOC_SECRET)
        nonce = "1616492376594"
        postdata = ("nonce=1616492376594&ordertype=limit&pair=XBTUSD"
                    "&price=37500&type=buy&volume=1.25")
        self.assertEqual(
            exchange.sign("/0/private/AddOrder", postdata, nonce),
            "4/dpxb3iT4tp/ZCVEwSnEsLxx0bqyhLpdfOpc6fn7OR8+UClSV5n9E6aSS8MPtnRfp32bAb0nmbRn6H8ndwLUQ==",
        )

    def test_invalid_secret_rejected(self):
        with self.assertRaises(ValueError):
            KrakenExchange(api_key="key", api_secret="geen-base64!!")
        with self.assertRaises(ValueError):
            KrakenExchange(api_key="", api_secret=self.DOC_SECRET)

    def test_parse_ohlc(self):
        rows = [[1700000000, "100.0", "105.0", "99.0", "102.0", "101.0", "12.5", 42]]
        candles = parse_ohlc(rows)
        self.assertEqual(len(candles), 1)
        self.assertEqual(candles[0].close, 102.0)
        self.assertEqual(candles[0].volume, 12.5)
        self.assertEqual(candles[0].open_time.year, 2023)

    def test_unsupported_interval_rejected(self):
        with self.assertRaises(ValueError):
            kraken_fetch_candles("XBTUSD", interval="3m")

    def test_free_balance_handles_kraken_asset_names(self):
        exchange = KrakenExchange(api_key="key", api_secret=self.DOC_SECRET)
        balances = {"ZUSD": "150.5", "XXBT": "0.25", "USDT": "42.0"}
        with mock.patch.object(exchange, "_private", return_value=balances):
            self.assertEqual(exchange.free_balance("USD"), 150.5)
            self.assertEqual(exchange.free_balance("BTC"), 0.25)
            self.assertEqual(exchange.free_balance("USDT"), 42.0)
            self.assertEqual(exchange.free_balance("EUR"), 0.0)

    def test_round_quantity_uses_lot_decimals(self):
        exchange = KrakenExchange(api_key="key", api_secret=self.DOC_SECRET)
        exchange._filters["XBTUSD"] = {"step_size": "0.001", "min_qty": "0.0001",
                                       "min_notional": "0.5", "pair_key": "XXBTZUSD"}
        self.assertAlmostEqual(exchange.round_quantity("XBTUSD", 0.123456), 0.123)

    def test_get_market(self):
        self.assertEqual(get_market("binance").name, "binance")
        self.assertEqual(get_market("kraken").name, "kraken")
        with self.assertRaises(ValueError):
            get_market("bitvavo")

    def test_bot_uses_kraken_market(self):
        bot = TradeBot(BotConfig(exchange="kraken", symbol="XBTUSD"))
        self.assertEqual(bot.market.name, "kraken")
        self.assertEqual(bot.quote, "USD")


class TestLiveBot(unittest.TestCase):
    @staticmethod
    def _candles_with_buy_signal(config: BotConfig) -> list[Candle]:
        """Serie die precies op de laatste candle een SMA-koopsignaal geeft."""
        from trade_bot.strategy import Signal, build_strategy
        closes = [100.0 - i for i in range(30)] + [80.0 + 3 * i for i in range(15)]
        strategy = build_strategy(config)
        for i in range(len(closes)):
            if strategy.signal(closes[: i + 1]) is Signal.BUY:
                return make_candles(closes[: i + 1])
        raise AssertionError("geen koopsignaal in testreeks")

    def test_live_buy_routes_through_exchange(self):
        config = BotConfig(strategy="sma_cross", fast_period=5, slow_period=20,
                           start_cash=1000.0, max_order=50.0)
        exchange = FakeExchange(balance=1000.0, price=100.0)
        bot = TradeBot(config, exchange=exchange)
        candles = self._candles_with_buy_signal(config)
        with patch_market(bot, candles):
            bot.step()
        self.assertEqual(len(exchange.orders), 1)
        side, amount = exchange.orders[0]
        self.assertEqual(side, "BUY")
        self.assertLessEqual(amount, config.max_order)  # bestedingslimiet gerespecteerd
        self.assertTrue(bot.portfolio.in_position)

    def test_live_budget_capped_by_exchange_balance(self):
        config = BotConfig(start_cash=10_000.0)
        bot = TradeBot(config, exchange=FakeExchange(balance=250.0))
        self.assertAlmostEqual(bot.portfolio.cash, 250.0)

    def test_live_requires_balance(self):
        with self.assertRaises(ExchangeError):
            TradeBot(BotConfig(), exchange=FakeExchange(balance=0.0))

    def test_paper_mode_never_touches_exchange(self):
        config = BotConfig(strategy="sma_cross", fast_period=5, slow_period=20)
        bot = TradeBot(config)  # geen exchange
        candles = self._candles_with_buy_signal(config)
        with patch_market(bot, candles):
            bot.step()
        self.assertTrue(bot.portfolio.in_position)  # paper-aankoop gedaan


class TestState(unittest.TestCase):
    def _state_path(self) -> str:
        import tempfile
        d = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, d, True)
        return f"{d}/state.json"

    def test_roundtrip_restores_position_and_trades(self):
        path = self._state_path()
        bot = TradeBot(BotConfig(symbol="BTCUSDT"))
        bot.portfolio.buy(100.0, 0.5)
        bot.equity_history.extend([10000.0, 10100.0])
        save_state(bot, path)

        restored = TradeBot(BotConfig(symbol="BTCUSDT"), state_file=path)
        self.assertTrue(restored.portfolio.in_position)
        self.assertAlmostEqual(restored.portfolio.entry_price, 100.0)
        self.assertAlmostEqual(restored.portfolio.cash, bot.portfolio.cash)
        self.assertEqual(len(restored.portfolio.trades), 1)
        self.assertEqual(list(restored.equity_history), [10000.0, 10100.0])

    def test_symbol_mismatch_starts_clean(self):
        path = self._state_path()
        bot = TradeBot(BotConfig(symbol="BTCUSDT"))
        bot.portfolio.buy(100.0, 0.5)
        save_state(bot, path)

        other = TradeBot(BotConfig(symbol="ETHUSDT"))
        self.assertFalse(load_state(other, path))
        self.assertFalse(other.portfolio.in_position)

    def test_missing_file_is_fine(self):
        bot = TradeBot(BotConfig())
        self.assertFalse(load_state(bot, "/nonexistent/state.json"))

    def test_live_mode_keeps_exchange_cash(self):
        path = self._state_path()
        paper = TradeBot(BotConfig(symbol="BTCUSDT", start_cash=10_000.0))
        paper.portfolio.buy(100.0, 0.5)
        save_state(paper, path)

        live = TradeBot(BotConfig(symbol="BTCUSDT", start_cash=10_000.0),
                        exchange=FakeExchange(balance=300.0), state_file=path)
        self.assertAlmostEqual(live.portfolio.cash, 300.0)  # exchange-saldo leidend
        self.assertTrue(live.portfolio.in_position)          # maar positie hersteld


class TestNotify(unittest.TestCase):
    def test_send_posts_to_telegram_api(self):
        notifier = TelegramNotifier("TOKEN123", "42")
        with mock.patch("trade_bot.notify.requests.post") as post:
            post.return_value.status_code = 200
            self.assertTrue(notifier.send("hallo"))
        url = post.call_args.args[0]
        payload = post.call_args.kwargs["json"]
        self.assertIn("botTOKEN123/sendMessage", url)
        self.assertEqual(payload, {"chat_id": "42", "text": "hallo"})

    def test_send_never_raises_on_network_error(self):
        import requests as req
        notifier = TelegramNotifier("t", "c")
        with mock.patch("trade_bot.notify.requests.post", side_effect=req.ConnectionError):
            self.assertFalse(notifier.send("hallo"))

    def test_error_cooldown_limits_spam(self):
        notifier = TelegramNotifier("t", "c")
        with mock.patch("trade_bot.notify.requests.post") as post:
            post.return_value.status_code = 200
            self.assertTrue(notifier.send_error("fout 1"))
            self.assertFalse(notifier.send_error("fout 2"))  # binnen afkoelperiode
        self.assertEqual(post.call_count, 1)

    def test_from_env(self):
        with mock.patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "t",
                                            "TELEGRAM_CHAT_ID": "c"}):
            self.assertIsNotNone(TelegramNotifier.from_env())
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertIsNone(TelegramNotifier.from_env())


class TestAutoStrategy(unittest.TestCase):
    def test_relearn_picks_best_strategy(self):
        config = BotConfig(strategy="auto", fast_period=5, slow_period=20)
        bot = TradeBot(config)
        closes = [100.0 - i for i in range(30)] + [70.0 + 2 * i for i in range(40)] \
            + [150.0 - 2 * i for i in range(20)]
        candles = make_candles(closes)

        # bepaal onafhankelijk welke strategie op deze data wint
        from dataclasses import replace
        from trade_bot.strategy import STRATEGY_NAMES
        expected = max(STRATEGY_NAMES,
                       key=lambda n: run_backtest(candles, replace(config, strategy=n))
                       .total_return_pct)

        with patch_market(bot, candles):
            bot.relearn()
        self.assertEqual(bot.active_strategy, expected)

    def test_no_switch_while_in_position(self):
        config = BotConfig(strategy="auto")
        bot = TradeBot(config)
        bot.portfolio.buy(100.0, 0.5)
        with mock.patch.object(bot, "relearn") as relearn:
            bot._maybe_relearn()
        relearn.assert_not_called()

    def test_fixed_strategy_never_relearns(self):
        bot = TradeBot(BotConfig(strategy="sma_cross"))
        with mock.patch.object(bot, "relearn") as relearn:
            bot._maybe_relearn()
        relearn.assert_not_called()

    def test_notifier_receives_trade_message(self):
        messages = []

        class FakeNotifier:
            def send(self, text):
                messages.append(text)

            def send_error(self, text):
                messages.append(text)

        config = BotConfig(strategy="sma_cross", fast_period=5, slow_period=20)
        bot = TradeBot(config, notifier=FakeNotifier())
        candles = TestLiveBot._candles_with_buy_signal(config)
        with patch_market(bot, candles):
            bot.step()
        self.assertTrue(any("Gekocht" in m for m in messages))


class TestDashboard(unittest.TestCase):
    def _dashboard(self) -> Dashboard:
        bot = TradeBot(BotConfig(), exchange=FakeExchange(balance=500.0))
        dashboard = Dashboard(bot, port=0)  # poort 0 = vrije poort, server niet gestart
        self.addCleanup(dashboard.server.server_close)
        return dashboard

    def test_status_reports_bot_state(self):
        dashboard = self._dashboard()
        status = dashboard.status()
        self.assertEqual(status["mode"], "TESTNET")
        self.assertFalse(status["paused"])
        self.assertEqual(status["cash"], 500.0)
        self.assertEqual(status["quote"], "USDT")
        self.assertEqual(status["trades"], [])

    def test_pause_resume_and_unknown_action(self):
        dashboard = self._dashboard()
        self.assertEqual(dashboard.handle_action("pause"), {"ok": True})
        self.assertTrue(dashboard.bot.paused)
        self.assertEqual(dashboard.handle_action("resume"), {"ok": True})
        self.assertFalse(dashboard.bot.paused)
        self.assertIn("error", dashboard.handle_action("hack"))

    def test_panic_sells_position_and_pauses(self):
        dashboard = self._dashboard()
        bot = dashboard.bot
        bot.last_price = 100.0
        exchange = bot.exchange
        bot.portfolio.record_buy(price=100.0, quantity=1.0, quote_spent=100.0)
        dashboard.handle_action("panic")
        self.assertTrue(bot.paused)
        self.assertFalse(bot.portfolio.in_position)
        self.assertEqual(exchange.orders[-1][0], "SELL")

    def test_http_roundtrip_with_token_check(self):
        import urllib.request

        dashboard = self._dashboard()
        dashboard.start()
        self.addCleanup(dashboard.stop)
        base = f"http://127.0.0.1:{dashboard.port}"

        with urllib.request.urlopen(f"{base}/api/status", timeout=5) as resp:
            status = __import__("json").loads(resp.read())
        self.assertEqual(status["mode"], "TESTNET")

        # verkeerde toegangscode → 403, bot blijft draaien
        request = urllib.request.Request(
            f"{base}/api/action", method="POST",
            data=b'{"action": "pause", "token": "fout"}',
            headers={"Content-Type": "application/json"})
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(request, timeout=5)
        self.assertEqual(ctx.exception.code, 403)
        self.assertFalse(dashboard.bot.paused)

        # juiste toegangscode → gepauzeerd
        body = ('{"action": "pause", "token": "%s"}' % dashboard.token).encode()
        request = urllib.request.Request(
            f"{base}/api/action", method="POST", data=body,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
        self.assertTrue(dashboard.bot.paused)


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
