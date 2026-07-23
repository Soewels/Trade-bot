"""Configuration for the multi-instrument trading bot (bot/).

API keys are read from a `.env` file in the project root (see `.env.example`).
Strategy parameters live here so they can be tuned without touching code.

Two market profiles, selected with BOT_MARKET in .env:

  eu (default) — European exchanges, everything in EUR:
      UCITS ETFs/ETCs on Xetra via Interactive Brokers (TWS/IB Gateway)
      and BTC/EUR via Kraken. PRIIPs-proof: no US-domiciled ETFs.
  us           — the original Alpaca setup: SPY, QQQ, GLD, USO, BTC/USD.
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

# --- market selection ---------------------------------------------------------
BOT_MARKET = os.environ.get("BOT_MARKET", "eu").lower()

# --- Alpaca (us profile) --------------------------------------------------------
ALPACA_API_KEY = os.environ.get("ALPACA_API_KEY", "")
ALPACA_API_SECRET = os.environ.get("ALPACA_API_SECRET", "")
# Paper trading by default; switch to https://api.alpaca.markets for live.
ALPACA_BASE_URL = os.environ.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
# Market data feed: "iex" is free; "sip" needs a paid data subscription.
ALPACA_DATA_FEED = os.environ.get("ALPACA_DATA_FEED", "iex")

# --- Interactive Brokers (eu profile) ---------------------------------------------
# Requires a running TWS or IB Gateway. Default port 7497 = TWS paper account;
# 7496 = TWS live, 4002 = IB Gateway paper, 4001 = IB Gateway live.
IBKR_HOST = os.environ.get("IBKR_HOST", "127.0.0.1")
IBKR_PORT = int(os.environ.get("IBKR_PORT", "7497"))
IBKR_CLIENT_ID = int(os.environ.get("IBKR_CLIENT_ID", "17"))
# Shorting UCITS ETFs needs a margin account; off by default.
IBKR_ALLOW_SHORTS = os.environ.get("IBKR_ALLOW_SHORTS", "0") == "1"

# --- Kraken (eu profile, BTC/EUR) ---------------------------------------------------
# Without keys the BTC leg runs in paper mode: real prices, simulated fills.
KRAKEN_API_KEY = os.environ.get("KRAKEN_API_KEY", "")
KRAKEN_API_SECRET = os.environ.get("KRAKEN_API_SECRET", "")
KRAKEN_PAPER_CASH = float(os.environ.get("KRAKEN_PAPER_CASH", "10000"))

# --- US stock screener (eu profile, needs IBKR) ---------------------------------------
# The bot finds liquid US stocks itself via the IBKR market scanner and adds
# them to the mean-reversion strategy (individual US stocks are PRIIPs-exempt,
# unlike US ETFs). Set US_STOCK_COUNT=0 to disable.
US_STOCK_COUNT = int(os.environ.get("US_STOCK_COUNT", "3"))
US_STOCK_THRESHOLD = 2.0            # entry threshold in std devs (wider: single
                                    # stocks are noisier than index trackers)
US_STOCK_MIN_PRICE = 10.0           # skip penny-ish stocks
US_STOCK_MIN_MARKET_CAP_MUSD = 10_000.0   # >= $10 billion market cap
US_STOCK_MIN_DOLLAR_VOLUME = 50e6   # >= $50M average daily dollar volume
US_STOCK_RESCAN_DAYS = 7            # refresh the universe weekly

# --- Telegram ----------------------------------------------------------------------
# Prefix voor alle meldingen van deze bot, zodat je ze in dezelfde chat kunt
# onderscheiden van de oude crypto-bot. Leeg ("") = geen prefix.
TELEGRAM_PREFIX = os.environ.get("TELEGRAM_PREFIX", "[Multi-bot]")

# --- risk management ---------------------------------------------------------------
ATR_PERIOD = 14
RISK_PER_TRADE = 0.01          # a 1 ATR adverse move == 1% of account equity
MAX_NOTIONAL_FRACTION = 0.95   # never spend more than this fraction of buying power

# --- market profiles ------------------------------------------------------------------
# Instrument notes for "eu" (all EUR, Xetra = IBKR exchange code IBIS):
#   SXR8 = iShares Core S&P 500 UCITS ETF (SPY equivalent)
#   SXRV = iShares Nasdaq 100 UCITS ETF   (QQQ equivalent)
#   4GLD = Xetra-Gold ETC                 (GLD equivalent)
#   OD7F = WisdomTree WTI Crude Oil ETC   (USO equivalent)
# Different listing preferred (e.g. CSPX on Euronext Amsterdam)? Change the
# symbol + exchange here and verify the combination in TWS first.
MARKETS = {
    "eu": {
        "timezone": "Europe/Amsterdam",   # daily P&L calendar
        "instruments": {
            "SXR8": {"broker": "ibkr", "exchange": "IBIS", "currency": "EUR"},
            "SXRV": {"broker": "ibkr", "exchange": "IBIS", "currency": "EUR"},
            "4GLD": {"broker": "ibkr", "exchange": "IBIS", "currency": "EUR"},
            "OD7F": {"broker": "ibkr", "exchange": "IBIS", "currency": "EUR"},
            "BTC/EUR": {"broker": "kraken", "pair": "XBTEUR"},
        },
        "mean_reversion_symbols": {"SXR8": 1.5, "SXRV": 1.8},
        "momentum_symbols": ["BTC/EUR"],
        "trend_symbols": ["4GLD", "OD7F"],
        # if both are long, no new crypto longs (correlation filter)
        "risk_on_pair": ("SXR8", "SXRV"),
        "correlation_blocked_symbol": "BTC/EUR",
    },
    "us": {
        "timezone": "America/New_York",
        "instruments": {
            "SPY": {"broker": "alpaca"},
            "QQQ": {"broker": "alpaca"},
            "GLD": {"broker": "alpaca"},
            "USO": {"broker": "alpaca"},
            "BTC/USD": {"broker": "alpaca", "crypto": True},
        },
        "mean_reversion_symbols": {"SPY": 1.5, "QQQ": 1.8},
        "momentum_symbols": ["BTC/USD"],
        "trend_symbols": ["GLD", "USO"],
        "risk_on_pair": ("SPY", "QQQ"),
        "correlation_blocked_symbol": "BTC/USD",
    },
}

if BOT_MARKET not in MARKETS:
    raise ValueError(f"BOT_MARKET must be one of {sorted(MARKETS)}, got: {BOT_MARKET}")

MARKET = MARKETS[BOT_MARKET]
INSTRUMENTS = MARKET["instruments"]
TIMEZONE = MARKET["timezone"]
RISK_ON_PAIR = MARKET["risk_on_pair"]
CORRELATION_BLOCKED_SYMBOL = MARKET["correlation_blocked_symbol"]

# --- strategy parameters -----------------------------------------------------------------
MEAN_REVERSION = {
    # symbol -> entry threshold in standard deviations
    "symbols": MARKET["mean_reversion_symbols"],
    "period": 20,               # SMA / stdev lookback
    "timeframe_minutes": 15,
}

MOMENTUM_BREAKOUT = {
    "symbols": MARKET["momentum_symbols"],
    "period": 20,               # breakout high/low lookback
    "volume_mult": 1.5,         # volume must be >= 1.5x the 20-period average
    "timeframe_minutes": 60,
    "trail_atr_mult": 2.0,      # trailing stop distance in ATRs
}

TREND_FOLLOWING = {
    "symbols": MARKET["trend_symbols"],
    "fast_period": 50,          # fast EMA
    "slow_period": 200,         # slow EMA
    "timeframe_minutes": 240,
    "trail_atr_mult": 3.0,
}

# --- runtime -----------------------------------------------------------------------------
POLL_SECONDS = 30              # main loop wake-up interval (stop checks)
BAR_FETCH_LIMIT = 500          # history fetched per evaluation (>= 201 for EMA200)

TRADES_CSV = str(PROJECT_ROOT / "trades.csv")
DAILY_PNL_CSV = str(PROJECT_ROOT / "daily_pnl.csv")
STATE_FILE = str(PROJECT_ROOT / "alpaca_state.json")
KRAKEN_PAPER_STATE_FILE = str(PROJECT_ROOT / "kraken_paper.json")
