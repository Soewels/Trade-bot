"""Test welke ETF-noteringen bij IBKR bestaan én gratis (vertraagde) data
leveren, via de draaiende IB Gateway.

Gebruik op de server:
    /opt/multi-bot/venv/bin/python deploy/ibkr_datatest.py
"""

from ib_async import IB, Stock

TESTS = [
    ("SXR8", "IBIS2", "EUR", "S&P500 Xetra (huidig)"),
    ("VUSA", "AEB", "EUR", "Vanguard S&P500 A'dam"),
    ("CSPX", "AEB", "USD", "iShares S&P500 A'dam"),
    ("EQQQ", "AEB", "EUR", "Invesco Nasdaq A'dam"),
    ("CNDX", "LSEETF", "USD", "iShares Nasdaq Londen"),
    ("SGLD", "LSEETF", "USD", "Invesco goud Londen"),
    ("IGLN", "LSEETF", "USD", "iShares goud Londen"),
    ("GLDA", "AEB", "EUR", "Xtrackers goud A'dam"),
    ("CRUDP", "SBF", "EUR", "WisdomTree olie Parijs"),
    ("CRUD", "LSE", "USD", "WisdomTree olie Londen"),
]


def main() -> None:
    ib = IB()
    ib.connect("127.0.0.1", 4002, clientId=58, timeout=20)
    ib.reqMarketDataType(3)  # vertraagde data volstaat
    print("=" * 60)
    for symbol, exchange, currency, naam in TESTS:
        label = f"{naam} ({symbol}/{exchange}/{currency})"
        try:
            result = ib.qualifyContracts(Stock(symbol, exchange, currency))
            qualified = [c for c in (result or []) if c is not None]
            if not qualified:
                print(f"{label}: NIET GEVONDEN")
                continue
            bars = ib.reqHistoricalData(
                qualified[0], endDateTime="", durationStr="3 D",
                barSizeSetting="1 day", whatToShow="TRADES",
                useRTH=True, formatDate=2)
            if bars:
                print(f"{label}: OK — {len(bars)} daily bars, "
                      f"laatste koers {bars[-1].close}")
            else:
                print(f"{label}: gevonden maar GEEN DATA")
        except Exception as exc:
            print(f"{label}: FOUT {exc}")
    print("=" * 60)
    ib.disconnect()


if __name__ == "__main__":
    main()
