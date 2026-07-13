"""Configuration for the Alpaca multi-instrument trading bot.

API keys are read from a `.env` file in the project root (see `.env.example`).
Strategy parameters live here so they can be tuned without touching code.
"""

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader: KEY=VALUE lines, '#' comments, no quoting rules."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        os.environ.setdefault(key, value)


_load_dotenv(PROJECT_ROOT / ".env")

# --- Alpaca API -------------------------------------------------------------
ALPACA_API_KEY = os.environ.get("ALPACA_API_KEY", "")
ALPACA_API_SECRET = os.environ.get("ALPACA_API_SECRET", "")
# Paper trading by default; switch to https://api.alpaca.markets for live.
ALPACA_BASE_URL = os.environ.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
# Market data feed: "iex" is free; "sip" needs a paid data subscription.
ALPACA_DATA_FEED = os.environ.get("ALPACA_DATA_FEED", "iex")

# --- Instruments ------------------------------------------------------------
CRYPTO_SYMBOLS = {"BTC/USD"}

# --- Risk management --------------------------------------------------------
ATR_PERIOD = 14
RISK_PER_TRADE = 0.01          # a 1 ATR adverse move == 1% of account equity
MAX_NOTIONAL_FRACTION = 0.95   # never spend more than this fraction of buying power

# --- Strategy parameters ----------------------------------------------------
MEAN_REVERSION = {
    # symbol -> entry threshold in standard deviations
    "symbols": {"SPY": 1.5, "QQQ": 1.8},
    "period": 20,               # SMA / stdev lookback
    "timeframe_minutes": 15,
}

MOMENTUM_BREAKOUT = {
    "symbols": ["BTC/USD"],
    "period": 20,               # breakout high/low lookback
    "volume_mult": 1.5,         # volume must be >= 1.5x the 20-period average
    "timeframe_minutes": 60,
    "trail_atr_mult": 2.0,      # trailing stop distance in ATRs
}

TREND_FOLLOWING = {
    "symbols": ["GLD", "USO"],
    "fast_period": 50,          # fast EMA
    "slow_period": 200,         # slow EMA
    "timeframe_minutes": 240,
    "trail_atr_mult": 3.0,
}

# --- Runtime ----------------------------------------------------------------
POLL_SECONDS = 30              # main loop wake-up interval (stop checks)
BAR_FETCH_LIMIT = 500          # history fetched per evaluation (>= 201 for EMA200)

TRADES_CSV = str(PROJECT_ROOT / "trades.csv")
DAILY_PNL_CSV = str(PROJECT_ROOT / "daily_pnl.csv")
STATE_FILE = str(PROJECT_ROOT / "alpaca_state.json")
