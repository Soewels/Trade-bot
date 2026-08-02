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

# --- stock screener (eu profile, needs IBKR) ------------------------------------------
# The bot finds liquid stocks itself via the IBKR market scanner and adds
# them to the mean-reversion strategy (individual stocks are PRIIPs-exempt,
# unlike US ETFs). Set US_STOCK_COUNT=0 to disable.
US_STOCK_COUNT = int(os.environ.get("US_STOCK_COUNT", "3"))

# Regio's waarin de scanner zoekt (IBKR-scannerlocaties per regio).
# Let op: Londen (LSE) is bewust uitgesloten — aandelen noteren daar in
# pence, wat de positiegrootte 100x zou verstoren.
REGION_LOCATIONS = {
    "US": ["STK.US.MAJOR"],
    "EU": ["STK.EU.IBIS", "STK.EU.AEB", "STK.EU.SBF"],
    "ASIA": ["STK.HK.SEHK", "STK.JP.TSEJ"],
}


def parse_stock_regions(raw: str) -> list[str]:
    regions = []
    for part in raw.split(","):
        part = part.strip().upper()
        if not part:
            continue
        if part not in REGION_LOCATIONS:
            raise ValueError(f"onbekende aandelenregio '{part}': "
                             f"kies uit {', '.join(REGION_LOCATIONS)}")
        if part not in regions:
            regions.append(part)
    return regions or ["US"]


STOCK_REGIONS = parse_stock_regions(os.environ.get("STOCK_REGIONS", "US,EU,ASIA"))
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

# Aparte potjes (in EUR/basisvaluta): maximaal totaalbedrag aan open posities
# per categorie. 0 = geen limiet (dan begrenst alleen het brokersaldo).
CRYPTO_BUDGET = float(os.environ.get("CRYPTO_BUDGET", "0"))
STOCKS_BUDGET = float(os.environ.get("STOCKS_BUDGET", "0"))

# Maximum per lósse positie (EUR, 0 = geen aparte limiet). Zonder deze limiet
# kan een rustig instrument (kleine ATR) in z'n eentje het hele potje vullen;
# hiermee garandeer je spreiding over meerdere munten/aandelen tegelijk.
CRYPTO_MAX_PER_POSITION = float(os.environ.get("CRYPTO_MAX_PER_POSITION", "0"))
STOCKS_MAX_PER_POSITION = float(os.environ.get("STOCKS_MAX_PER_POSITION", "0"))

# --- crypto-instrumenten (eu-profiel, via Kraken) -----------------------------------
# Komma-gescheiden lijst van munten die de breakout-strategie handelt, bv.:
#   CRYPTO_SYMBOLS=BTC/EUR,ETH/EUR,DOGE/EUR,PEPE/EUR
# Alles wat Kraken in EUR aanbiedt werkt; de bot controleert de paren bij het
# opstarten. Let op met meme coins: de ATR-sizing maakt de posities vanzelf
# klein, maar sprongen kunnen over een stop heen schieten.

def kraken_pair(symbol: str) -> str:
    """Kraken-paarcode voor een BASE/QUOTE-symbool (BTC heet er XBT, DOGE XDG)."""
    base, _, quote = symbol.partition("/")
    aliases = {"BTC": "XBT", "DOGE": "XDG"}
    return aliases.get(base, base) + quote


def parse_crypto_symbols(raw: str) -> list[str]:
    symbols: list[str] = []
    for part in raw.split(","):
        part = part.strip().upper()
        if not part:
            continue
        if "/" not in part:
            raise ValueError(f"crypto-symbool '{part}' mist een '/': "
                             "gebruik bv. BTC/EUR of DOGE/EUR")
        if part not in symbols:
            symbols.append(part)
    if not symbols:
        raise ValueError("CRYPTO_SYMBOLS bevat geen geldig symbool")
    return symbols


CRYPTO_SYMBOLS = parse_crypto_symbols(os.environ.get("CRYPTO_SYMBOLS", "BTC/EUR"))


def parse_crypto_timeframe(raw: str) -> int:
    """Candle-grootte (minuten) voor de crypto-handel én -scanner.

    Kleiner = alerter op snelle bewegingen (meme coins), maar ook meer
    valse uitbraken en meer trades. De scanner meet zijn momentum in
    24/48 candles van deze maat: bij 60 is dat 24/48 uur, bij 15 zo'n
    6/12 uur."""
    minutes = int(raw)
    if minutes not in (5, 15, 30, 60, 240):
        raise ValueError("CRYPTO_TIMEFRAME_MINUTES moet 5, 15, 30, 60 of 240 zijn")
    return minutes


CRYPTO_TIMEFRAME_MINUTES = parse_crypto_timeframe(
    os.environ.get("CRYPTO_TIMEFRAME_MINUTES", "60"))

# Zelfzoekende crypto-selectie: de bot scant alle Kraken-EUR-munten en kiest
# de sterkste stijgers zelf. 0 = uit (dan geldt de vaste CRYPTO_SYMBOLS-lijst;
# die lijst blijft in auto-modus het startpunt tot de eerste scan klaar is).
CRYPTO_AUTO_COUNT = int(os.environ.get("CRYPTO_AUTO_COUNT", "5"))
CRYPTO_MIN_EUR_VOLUME = float(os.environ.get("CRYPTO_MIN_EUR_VOLUME", "2000000"))
CRYPTO_RESCAN_HOURS = float(os.environ.get("CRYPTO_RESCAN_HOURS", "12"))

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
            **{sym: {"broker": "kraken", "pair": kraken_pair(sym)}
               for sym in CRYPTO_SYMBOLS},
        },
        "mean_reversion_symbols": {"SXR8": 1.5, "SXRV": 1.8},
        "momentum_symbols": list(CRYPTO_SYMBOLS),
        "trend_symbols": ["4GLD", "OD7F"],
        # if both are long, no new crypto longs (correlation filter)
        "risk_on_pair": ("SXR8", "SXRV"),
        "correlation_blocked_symbols": set(CRYPTO_SYMBOLS),
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
        "correlation_blocked_symbols": {"BTC/USD"},
    },
}

if BOT_MARKET not in MARKETS:
    raise ValueError(f"BOT_MARKET must be one of {sorted(MARKETS)}, got: {BOT_MARKET}")

MARKET = MARKETS[BOT_MARKET]
INSTRUMENTS = MARKET["instruments"]
TIMEZONE = MARKET["timezone"]
RISK_ON_PAIR = MARKET["risk_on_pair"]
CORRELATION_BLOCKED_SYMBOLS = MARKET["correlation_blocked_symbols"]

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
    "timeframe_minutes": CRYPTO_TIMEFRAME_MINUTES,
    "trail_atr_mult": 2.0,      # trailing stop distance in ATRs
}

TREND_FOLLOWING = {
    "symbols": MARKET["trend_symbols"],
    "fast_period": 50,          # fast EMA
    "slow_period": 200,         # slow EMA
    "timeframe_minutes": 240,
    "trail_atr_mult": 3.0,
}

# --- dashboard ---------------------------------------------------------------------------
# Mobiel dashboard (meekijken + pauze/noodstop). Standaard alleen op localhost:
# op een server kijk je mee via een SSH-tunnel of Tailscale. Zet WEB_HOST=0.0.0.0
# alleen als je het risico begrijpt; de knoppen zijn met een code beveiligd,
# meekijken niet. WEB_DASHBOARD=0 zet het dashboard uit.
WEB_ENABLED = os.environ.get("WEB_DASHBOARD", "1") == "1"
WEB_HOST = os.environ.get("WEB_HOST", "127.0.0.1")
WEB_PORT = int(os.environ.get("WEB_PORT", "8081"))   # oude bot gebruikt 8080
WEB_CODE = os.environ.get("WEB_CODE", "")            # leeg = random code in de log

# --- runtime -----------------------------------------------------------------------------
POLL_SECONDS = 30              # main loop wake-up interval (stop checks)
BAR_FETCH_LIMIT = 500          # history fetched per evaluation (>= 201 for EMA200)

TRADES_CSV = str(PROJECT_ROOT / "trades.csv")
DAILY_PNL_CSV = str(PROJECT_ROOT / "daily_pnl.csv")
STATE_FILE = str(PROJECT_ROOT / "alpaca_state.json")
KRAKEN_PAPER_STATE_FILE = str(PROJECT_ROOT / "kraken_paper.json")
