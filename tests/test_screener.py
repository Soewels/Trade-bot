"""Unit tests for the worldwide stock screener (pure logic, no IBKR needed)."""

import unittest

import config
from bot.brokers.ibkr_broker import exchange_is_open, hours_for_primary_exchange
from bot.models import Bar
from bot.screener import find_liquid_stocks, merge_universe, rank_by_dollar_volume
from bot.strategies import MeanReversionStrategy


def daily_bars(close: float, volume: float, days: int = 30) -> list[Bar]:
    return [Bar(ts=float(i * 86400), open=close, high=close, low=close,
                close=close, volume=volume) for i in range(days)]


class RankingTests(unittest.TestCase):
    def test_ranks_by_dollar_volume_descending(self):
        bars = {
            "AAPL": daily_bars(close=200.0, volume=50e6),   # $10B/day
            "F": daily_bars(close=12.0, volume=60e6),       # $0.72B/day
            "NVDA": daily_bars(close=150.0, volume=200e6),  # $30B/day
        }
        ranked = rank_by_dollar_volume(bars, min_dollar_volume=50e6)
        self.assertEqual(ranked, ["NVDA", "AAPL", "F"])

    def test_drops_illiquid_and_empty(self):
        bars = {
            "AAPL": daily_bars(close=200.0, volume=50e6),
            "TINY": daily_bars(close=15.0, volume=100_000),  # $1.5M/day
            "NODATA": [],
        }
        ranked = rank_by_dollar_volume(bars, min_dollar_volume=50e6)
        self.assertEqual(ranked, ["AAPL"])

    def test_uses_recent_lookback_window(self):
        # Old bars are huge, recent bars tiny: recent window must decide.
        bars = daily_bars(close=100.0, volume=100e6, days=30)
        for bar in bars[-20:]:
            bar.volume = 1000.0
        ranked = rank_by_dollar_volume({"X": bars}, min_dollar_volume=50e6,
                                       lookback=20)
        self.assertEqual(ranked, [])

    def test_fx_converts_foreign_volume_to_base(self):
        bars = {
            "SONY": daily_bars(close=15000.0, volume=5e6),   # JPY: 75 mld lokaal
            "SAP": daily_bars(close=200.0, volume=400_000),  # EUR: 80M
        }
        fx = {"SONY": 0.006, "SAP": 1.0}   # 75 mld JPY -> 450M EUR
        ranked = rank_by_dollar_volume(bars, min_dollar_volume=50e6, fx=fx)
        self.assertEqual(ranked, ["SONY", "SAP"])
        # zonder fx zou SONY 75 mld 'EUR' lijken; met een lage fx valt hij af
        ranked = rank_by_dollar_volume(bars, min_dollar_volume=50e6,
                                       fx={"SONY": 0.0000001, "SAP": 1.0})
        self.assertEqual(ranked, ["SAP"])


class WorldwideScreenerTests(unittest.TestCase):
    def test_stock_regions_config(self):
        self.assertEqual(config.parse_stock_regions("us, eu"), ["US", "EU"])
        self.assertEqual(config.parse_stock_regions(""), ["US"])
        with self.assertRaises(ValueError):
            config.parse_stock_regions("MARS")
        for region in config.STOCK_REGIONS:
            self.assertIn(region, config.REGION_LOCATIONS)

    def test_hours_mapping_and_unknown_exchange(self):
        self.assertEqual(hours_for_primary_exchange("NYSE"), "US")
        self.assertEqual(hours_for_primary_exchange("SEHK"), "SEHK")
        self.assertIsNone(hours_for_primary_exchange("ONBEKEND"))
        # de gemapte sleutels bestaan ook echt in de openingstijden-tabel
        from datetime import datetime
        from zoneinfo import ZoneInfo
        noon = datetime(2026, 7, 14, 4, 0, tzinfo=ZoneInfo("UTC"))  # 12:00 HK
        self.assertTrue(exchange_is_open("SEHK", noon))

    def test_location_hours_fallback_covers_all_configured_locations(self):
        # elke scan-locatie uit de config heeft een terugval-tijdzone, en
        # die verwijst naar een echte sleutel in de openingstijden-tabel
        from bot.brokers.ibkr_broker import EXCHANGE_HOURS, LOCATION_TO_HOURS
        for region_locations in config.REGION_LOCATIONS.values():
            for location in region_locations:
                self.assertIn(location, LOCATION_TO_HOURS)
                self.assertIn(LOCATION_TO_HOURS[location], EXCHANGE_HOURS)

    def test_find_liquid_stocks_skips_gbp_and_unknown(self):
        class ScanStub:
            def __init__(self):
                self.instruments = {}

            def scan_most_active(self, min_price, cap, rows, locations):
                return [
                    {"symbol": "NVDA", "currency": "USD", "primaryExchange": "NASDAQ"},
                    {"symbol": "SAP", "currency": "EUR", "primaryExchange": "IBIS"},
                    {"symbol": "SHEL", "currency": "GBP", "primaryExchange": "LSE"},
                    {"symbol": "RAAR", "currency": "USD", "primaryExchange": "GEHEIM"},
                    # scanner liet primaryExchange leeg, maar de scan-locatie
                    # gaf een hours-hint mee: moet gewoon meedoen
                    {"symbol": "PLTR", "currency": "USD", "primaryExchange": "",
                     "hours": "US"},
                    # leeg én geen hint: veiligheidsfilter slaat over
                    {"symbol": "MYST", "currency": "USD", "primaryExchange": ""},
                ]

            def add_instrument(self, symbol, meta):
                self.instruments[symbol] = meta

            def to_base_rate(self, symbol):
                return 0.9 if self.instruments[symbol]["currency"] == "USD" else 1.0

            def fetch_bars(self, symbol, tf, limit):
                return daily_bars(close=100.0, volume=2e6)  # 200M lokaal

        broker = ScanStub()
        ranked, metas = find_liquid_stocks(broker, count=3, min_price=10,
                                           min_market_cap_musd=1e4,
                                           min_dollar_volume=50e6,
                                           locations=["STK.US.MAJOR"])
        self.assertEqual(set(ranked), {"NVDA", "SAP", "PLTR"})
        self.assertNotIn("SHEL", metas)   # GBP/pence uitgesloten
        self.assertNotIn("RAAR", metas)   # onbekende beurs uitgesloten
        self.assertNotIn("MYST", metas)   # leeg zonder hint uitgesloten
        self.assertEqual(metas["SAP"]["hours"], "IBIS")
        self.assertEqual(metas["PLTR"]["hours"], "US")  # hint uit scan-locatie
        self.assertEqual(metas["NVDA"]["currency"], "USD")
        # SAP (EUR, fx 1.0) komt boven de USD-aandelen (fx 0.9)
        self.assertEqual(ranked[0], "SAP")


class WorldwideFallbackTests(unittest.TestCase):
    def test_fallback_lists_are_consistent(self):
        from bot.brokers.ibkr_broker import EXCHANGE_HOURS
        for region, cands in config.REGION_FALLBACK_STOCKS.items():
            self.assertIn(region, config.REGION_LOCATIONS)
            for cand in cands:
                self.assertTrue(cand["symbol"])
                self.assertNotEqual(cand["currency"], "GBP")  # pence-regel
                self.assertIn(cand["hours"], EXCHANGE_HOURS)
                if cand["currency"] in ("HKD", "JPY"):  # Azië: vaste kavels
                    self.assertGreaterEqual(cand.get("lot_size", 0), 100)

    def test_extra_candidates_join_ranking_and_dedupe(self):
        class ScanStub:
            def __init__(self):
                self.instruments = {}

            def scan_most_active(self, min_price, cap, rows, locations):
                return [{"symbol": "NVDA", "currency": "USD",
                         "primaryExchange": "NASDAQ"}]

            def add_instrument(self, symbol, meta):
                self.instruments[symbol] = meta

            def to_base_rate(self, symbol):
                return 1.0

            def fetch_bars(self, symbol, tf, limit):
                return daily_bars(close=100.0, volume=2e6)

        extras = [
            {"symbol": "ASML", "exchange": "AEB", "currency": "EUR",
             "primaryExchange": "AEB", "hours": "AEB"},
            {"symbol": "700", "exchange": "SEHK", "currency": "HKD",
             "primaryExchange": "SEHK", "hours": "SEHK", "lot_size": 100},
            {"symbol": "NVDA", "currency": "USD",
             "primaryExchange": "NASDAQ"},  # dubbel: scanner wint
        ]
        broker = ScanStub()
        ranked, metas = find_liquid_stocks(broker, count=3, min_price=10,
                                           min_market_cap_musd=1e4,
                                           min_dollar_volume=50e6,
                                           locations=["STK.US.MAJOR"],
                                           extra_candidates=extras)
        self.assertEqual(set(ranked), {"NVDA", "ASML", "700"})
        self.assertEqual(metas["ASML"]["exchange"], "AEB")
        self.assertEqual(metas["700"]["lot_size"], 100)
        self.assertEqual(metas["NVDA"]["exchange"], "SMART")


class LotSizeTests(unittest.TestCase):
    def test_position_size_rounds_down_to_whole_lots(self):
        from bot.risk_manager import RiskManager
        risk = RiskManager()
        qty = risk.position_size(equity=100_000, price=40.0, atr=2.0,
                                 buying_power=100_000, fractional=False,
                                 lot_size=100)
        self.assertEqual(qty, 500.0)  # 1%/ATR = 500: precies 5 kavels

    def test_position_size_zero_when_lot_does_not_fit(self):
        from bot.risk_manager import RiskManager
        risk = RiskManager()
        qty = risk.position_size(equity=20_000, price=45.0, atr=1.0,
                                 buying_power=20_000, fractional=False,
                                 max_notional=500.0, lot_size=100)
        self.assertEqual(qty, 0.0)  # kavel kost 4.500 > limiet 500


class MergeUniverseTests(unittest.TestCase):
    def test_fills_free_slots_with_best_picks(self):
        universe = merge_universe(current=[], held=set(),
                                  picks=["NVDA", "AAPL", "MSFT", "AMZN"], count=3)
        self.assertEqual(universe, ["NVDA", "AAPL", "MSFT"])

    def test_held_positions_are_never_swapped_out(self):
        universe = merge_universe(current=["TSLA", "META"], held={"TSLA"},
                                  picks=["NVDA", "AAPL"], count=2)
        self.assertEqual(universe, ["TSLA", "NVDA"])  # META (flat) replaced

    def test_no_duplicates_when_pick_is_already_held(self):
        universe = merge_universe(current=["NVDA"], held={"NVDA"},
                                  picks=["NVDA", "AAPL"], count=2)
        self.assertEqual(universe, ["NVDA", "AAPL"])

    def test_more_held_than_count_keeps_all_held(self):
        universe = merge_universe(current=["A", "B", "C"], held={"A", "B", "C"},
                                  picks=["D"], count=2)
        self.assertEqual(universe, ["A", "B", "C"])


class DynamicSymbolTests(unittest.TestCase):
    def test_add_and_remove_symbol_at_runtime(self):
        strategy = MeanReversionStrategy({"SXR8": 1.5}, period=20)
        strategy.add_symbol("NVDA", 2.0)
        self.assertIn("NVDA", strategy.symbols)
        bars = [Bar(ts=i, open=100, high=100, low=100, close=100, volume=0)
                for i in range(24)] + [Bar(ts=24, open=80, high=80, low=80,
                                           close=80.0, volume=0)]
        signal = strategy.evaluate("NVDA", bars, None)
        self.assertEqual(signal.action, "long")
        strategy.remove_symbol("NVDA")
        self.assertNotIn("NVDA", strategy.symbols)
        self.assertNotIn("NVDA", strategy.thresholds)

    def test_add_symbol_rejects_bad_threshold(self):
        strategy = MeanReversionStrategy({"SXR8": 1.5}, period=20)
        with self.assertRaises(ValueError):
            strategy.add_symbol("NVDA", 0)


if __name__ == "__main__":
    unittest.main()
