"""Liquid US stock screener.

The bot finds its own tradable US stocks: the IBKR market scanner returns
the most active names, which are then ranked on average daily dollar
volume (close x volume) computed from real daily bars. The top `count`
stocks join the mean-reversion strategy — long and, with a margin account,
short. Rescreening happens every US_STOCK_RESCAN_DAYS; symbols with an
open position are never swapped out.
"""

import logging

from .models import Bar

log = logging.getLogger("alpaca_bot.screener")

# Instrument metadata for screened US stocks: SMART-routed, USD, US hours.
US_STOCK_META = {"exchange": "SMART", "currency": "USD", "hours": "US"}


def rank_by_dollar_volume(bars_by_symbol: dict[str, list[Bar]],
                          min_dollar_volume: float,
                          lookback: int = 20) -> list[str]:
    """Symbols ordered by average daily dollar volume, most liquid first.
    Symbols below `min_dollar_volume` are dropped."""
    scores: dict[str, float] = {}
    for symbol, bars in bars_by_symbol.items():
        if not bars:
            continue
        window = bars[-lookback:]
        avg = sum(b.close * b.volume for b in window) / len(window)
        if avg >= min_dollar_volume:
            scores[symbol] = avg
    return sorted(scores, key=scores.__getitem__, reverse=True)


def merge_universe(current: list[str], held: set[str], picks: list[str],
                   count: int) -> list[str]:
    """New stock universe after a rescan: symbols with an open position
    always stay; free slots are filled with the best new picks."""
    universe = [sym for sym in current if sym in held]
    for sym in picks:
        if len(universe) >= max(count, len(universe)):
            break
        if sym not in universe:
            universe.append(sym)
    return universe


def find_liquid_us_stocks(broker, count: int, min_price: float,
                          min_market_cap_musd: float,
                          min_dollar_volume: float) -> list[str]:
    """Scan for the most active US stocks and rank them by dollar volume.

    `broker` is an IBKRBroker (needs scan_most_active/add_instrument/
    fetch_bars). Returns the ranked candidate list, best first.
    """
    rows = max(count * 3, 10)
    candidates = broker.scan_most_active(min_price, min_market_cap_musd, rows)
    log.info("scanner returned %d candidates: %s",
             len(candidates), ", ".join(candidates))
    bars_by_symbol: dict[str, list[Bar]] = {}
    for symbol in candidates:
        broker.add_instrument(symbol, dict(US_STOCK_META))
        try:
            bars_by_symbol[symbol] = broker.fetch_bars(symbol, 1440, 30)
        except Exception as exc:
            log.warning("no daily bars for candidate %s: %s", symbol, exc)
    ranked = rank_by_dollar_volume(bars_by_symbol, min_dollar_volume)
    log.info("liquidity ranking (>= %.0fM dollar volume/day): %s",
             min_dollar_volume / 1e6, ", ".join(ranked) or "none")
    return ranked
