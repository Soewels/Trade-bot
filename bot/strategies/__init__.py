"""Strategy modules for the Alpaca bot."""

from .candidates import (BollingerDipStrategy, EmaCrossStrategy,
                         RsiDipStrategy)
from .mean_reversion import MeanReversionStrategy
from .momentum_breakout import MomentumBreakoutStrategy
from .trend_following import TrendFollowingStrategy

__all__ = [
    "BollingerDipStrategy",
    "EmaCrossStrategy",
    "MeanReversionStrategy",
    "MomentumBreakoutStrategy",
    "RsiDipStrategy",
    "TrendFollowingStrategy",
]
