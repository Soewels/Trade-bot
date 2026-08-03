"""Mini-backtester voor het strategie-lab.

Speelt een strategie na op historische candles met dezelfde regels als
de echte bot: 1% risico per trade (1-ATR-stop), trailing stop wanneer de
strategie er een heeft, en handelskosten per transactie. De uitkomst is
een genormaliseerde score waarmee kandidaten eerlijk vergeleken worden.
"""

from dataclasses import dataclass

from .indicators import atr_series
from .models import Bar

WARMUP_BARS = 60          # indicatoren eerst laten vollopen
RISK_PER_TRADE = 0.01
FEE_RATE = 0.0026         # Kraken taker-fee per transactie


@dataclass
class BacktestResult:
    total_return: float    # fractie, bv. 0.043 = +4,3%
    max_drawdown: float    # fractie, bv. 0.021 = 2,1% diepste dal
    trades: int

    @property
    def score(self) -> float:
        """Rendement met straf voor diepe dalen: rustig verdienen wint
        van wild op-en-neer."""
        return self.total_return - 0.5 * self.max_drawdown


def simulate(strategy, symbol: str, bars: list[Bar],
             atr_period: int = 14) -> BacktestResult:
    """Speel `strategy` na over `bars` (oudste eerst, alleen long)."""
    equity = 1.0
    peak_equity = 1.0
    max_drawdown = 0.0
    trades = 0

    qty = 0.0
    entry = 0.0
    stop = 0.0
    peak_price = 0.0
    atr_values = atr_series(bars, atr_period)

    def close_position(price: float) -> None:
        nonlocal equity, qty, peak_equity, max_drawdown, trades
        equity += qty * (price - entry) - qty * price * FEE_RATE
        qty = 0.0
        trades += 1
        peak_equity = max(peak_equity, equity)
        if peak_equity > 0:
            max_drawdown = max(max_drawdown, 1 - equity / peak_equity)

    for i in range(WARMUP_BARS, len(bars)):
        bar = bars[i]
        atr = atr_values[i]
        if qty > 0:
            # eerst risicobeheer, net als in de echte bot
            peak_price = max(peak_price, bar.high)
            if strategy.trail_atr_mult and atr:
                stop = max(stop, peak_price - strategy.trail_atr_mult * atr)
            if bar.low <= stop:
                close_position(stop)
                continue
        window = bars[:i + 1]
        side = "long" if qty > 0 else None
        signal = strategy.evaluate(symbol, window, side)
        if signal is None:
            continue
        if signal.action in ("exit", "short") and qty > 0:
            # crypto is spot-only: een short-signaal betekent "stap uit"
            close_position(bar.close)
        elif signal.action == "long" and qty == 0 and atr and atr > 0:
            entry = bar.close
            qty = min(RISK_PER_TRADE * equity / atr,   # 1 ATR = 1% equity
                      equity / entry)                   # geen hefboom
            equity -= qty * entry * FEE_RATE
            stop = entry - atr
            peak_price = entry
    if qty > 0:                                          # eindstand afrekenen
        close_position(bars[-1].close)
    return BacktestResult(total_return=equity - 1.0,
                          max_drawdown=max_drawdown, trades=trades)
