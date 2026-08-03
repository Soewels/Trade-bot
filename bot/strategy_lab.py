"""Strategie-lab: zoekt continu naar een betere crypto-strategie.

De bot heeft één actieve crypto-strategie (de "kampioen"). Het lab
backtest periodiek een vaste bibliotheek van kandidaten ("uitdagers") op
de meest recente candles van de eigen crypto-selectie. Om overfitting
tegen te gaan wordt de historie in twee helften geknipt: een uitdager
wint alleen als hij de kampioen in BEIDE helften verslaat, én in totaal
minstens `min_improvement` (25%) beter scoort. Pas dan schakelt de bot om.
"""

import logging

from .backtester import WARMUP_BARS, simulate
from .models import Bar
from .strategies.candidates import (BollingerDipStrategy, EmaCrossStrategy,
                                    RsiDipStrategy)
from .strategies.momentum_breakout import MomentumBreakoutStrategy

log = logging.getLogger("alpaca_bot")

# Elke helft moet na de warm-up nog genoeg candles overhouden om iets
# zinnigs te meten.
MIN_HALF_BARS = WARMUP_BARS + 40


def _breakout(name: str, period: int, volume_mult: float, trail: float):
    def make(symbols: list[str], timeframe_minutes: int):
        strategy = MomentumBreakoutStrategy(
            symbols, period=period, volume_mult=volume_mult,
            timeframe_minutes=timeframe_minutes, trail_atr_mult=trail)
        strategy.name = name  # variant herkenbaar in logs/trades.csv
        return strategy
    return make


# Naam -> fabriek. "momentum_breakout" is de vaste startkampioen en doet
# als kandidaat mee zodat er altijd een eerlijke nulmeting is.
CANDIDATES = {
    "momentum_breakout": _breakout("momentum_breakout", 20, 1.5, 2.0),
    "breakout_fast": _breakout("breakout_fast", 10, 1.2, 1.5),
    "breakout_slow": _breakout("breakout_slow", 40, 2.0, 3.0),
    "rsi_dip": lambda symbols, tf: RsiDipStrategy(symbols, timeframe_minutes=tf),
    "bollinger_dip": lambda symbols, tf: BollingerDipStrategy(
        symbols, timeframe_minutes=tf),
    "ema_cross": lambda symbols, tf: EmaCrossStrategy(symbols,
                                                      timeframe_minutes=tf),
}

# Leesbare namen voor Telegram en het dashboard.
LABELS_NL = {
    "momentum_breakout": "Breakout (standaard)",
    "breakout_fast": "Breakout snel",
    "breakout_slow": "Breakout traag",
    "rsi_dip": "RSI-dip",
    "bollinger_dip": "Bollinger-dip",
    "ema_cross": "EMA-kruising",
}


def build_candidate(name: str, symbols: list[str], timeframe_minutes: int):
    """Bouw een verse instantie van kandidaat `name`."""
    return CANDIDATES[name](symbols, timeframe_minutes)


def run_tournament(bars_by_symbol: dict[str, list[Bar]],
                   timeframe_minutes: int) -> dict[str, dict]:
    """Backtest alle kandidaten op beide helften van elke symboolhistorie.

    Retourneert per kandidaat: {"half1": score, "half2": score,
    "score": som, "trades": totaal}. Symbolen met te weinig historie
    worden overgeslagen; is er niets bruikbaars, dan is het resultaat {}.
    """
    halves: list[tuple[str, list[Bar]]] = []
    for symbol, bars in bars_by_symbol.items():
        mid = len(bars) // 2
        if mid < MIN_HALF_BARS:
            log.info("strategie-lab: te weinig historie voor %s "
                     "(%d candles), overgeslagen", symbol, len(bars))
            continue
        halves.append((symbol, bars[:mid]))
        halves.append((symbol, bars[mid:]))
    if not halves:
        return {}
    results: dict[str, dict] = {}
    for name in CANDIDATES:
        totals = {"half1": 0.0, "half2": 0.0, "trades": 0}
        for index, (symbol, bars) in enumerate(halves):
            strategy = build_candidate(name, [symbol], timeframe_minutes)
            outcome = simulate(strategy, symbol, bars)
            totals["half1" if index % 2 == 0 else "half2"] += outcome.score
            totals["trades"] += outcome.trades
        totals["score"] = totals["half1"] + totals["half2"]
        results[name] = totals
    return results


def choose(champion: str, results: dict[str, dict], min_improvement: float,
           min_trades: int) -> str:
    """Kies de nieuwe kampioen; bij twijfel wint de zittende kampioen.

    Een uitdager moet (1) genoeg trades hebben gedaan om iets te bewijzen,
    (2) de kampioen in beide helften verslaan en (3) in totaal minstens
    `min_improvement` (fractie van de kampioenscore, met een bodem)
    beter scoren.
    """
    if champion not in results:
        return champion
    base = results[champion]
    threshold = base["score"] + min_improvement * max(abs(base["score"]), 0.01)
    best_name, best_score = champion, None
    for name, res in results.items():
        if name == champion or res["trades"] < min_trades:
            continue
        if res["half1"] <= base["half1"] or res["half2"] <= base["half2"]:
            continue  # niet consistent beter: waarschijnlijk toeval
        if res["score"] < threshold:
            continue
        if best_score is None or res["score"] > best_score:
            best_name, best_score = name, res["score"]
    return best_name
