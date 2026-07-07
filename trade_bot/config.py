"""Configuratie voor de trade bot."""

from dataclasses import dataclass, field


@dataclass
class BotConfig:
    # Markt
    symbol: str = "BTCUSDT"          # handelspaar op Binance
    interval: str = "1h"             # candle-interval: 1m, 5m, 15m, 1h, 4h, 1d

    # Strategie
    strategy: str = "sma_cross"      # "sma_cross", "rsi" of "macd"
    fast_period: int = 10            # snelle SMA (sma_cross)
    slow_period: int = 30            # trage SMA (sma_cross)
    rsi_period: int = 14             # RSI-periode (rsi)
    rsi_oversold: float = 30.0       # koopsignaal onder deze RSI
    rsi_overbought: float = 70.0     # verkoopsignaal boven deze RSI
    macd_fast: int = 12              # snelle EMA (macd)
    macd_slow: int = 26              # trage EMA (macd)
    macd_signal: int = 9             # signaallijn-periode (macd)

    # Portfolio & risico
    start_cash: float = 10_000.0     # startkapitaal in quote-valuta (USDT)
    position_size: float = 0.95      # fractie van cash per aankoop (0..1)
    fee_rate: float = 0.001          # 0.1% handelskosten per order
    stop_loss: float = 0.05          # verkoop bij 5% verlies t.o.v. instap
    take_profit: float = 0.15        # verkoop bij 15% winst t.o.v. instap

    # Live loop
    poll_seconds: int = 60           # hoe vaak de bot nieuwe data ophaalt

    extra: dict = field(default_factory=dict)

    def validate(self) -> None:
        if self.fast_period >= self.slow_period:
            raise ValueError("fast_period moet kleiner zijn dan slow_period")
        if not 0 < self.position_size <= 1:
            raise ValueError("position_size moet tussen 0 en 1 liggen")
        if self.start_cash <= 0:
            raise ValueError("start_cash moet positief zijn")
        if self.macd_fast >= self.macd_slow:
            raise ValueError("macd_fast moet kleiner zijn dan macd_slow")
        if self.strategy not in ("sma_cross", "rsi", "macd"):
            raise ValueError(f"onbekende strategie: {self.strategy}")
