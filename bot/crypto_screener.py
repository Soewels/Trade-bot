"""Crypto-screener: de bot kiest zelf zijn munten op Kraken.

Werkwijze, elke CRYPTO_RESCAN_HOURS:
  1. alle EUR-handelsparen van Kraken ophalen (publieke API, geen keys);
  2. filteren op echte handel: minimaal CRYPTO_MIN_EUR_VOLUME omzet per
     24 uur, geen stablecoins, geen darkpool-paren;
  3. van de meest verhandelde munten de uurgrafiek scoren op momentum
     (stijging over 24u en 48u) met een bonus wanneer de koers tegen het
     20-uurs-hoogtepunt aanzit (breakout op komst of al bezig);
  4. de beste CRYPTO_AUTO_COUNT stijgers worden het actieve universum —
     munten met een open positie worden nooit weggeruild.

De breakout-strategie doet daarna de echte timing per munt.
"""

import logging
import time
from typing import Optional

from trade_bot import kraken

log = logging.getLogger("alpaca_bot.crypto_screener")

# Kraken noemt sommige munten anders dan de rest van de wereld.
KRAKEN_BASE_ALIASES = {"XBT": "BTC", "XDG": "DOGE"}

# Stablecoins en tokenized cash: stijgen nooit, dus nutteloos voor momentum.
STABLE_BASES = {"USDT", "USDC", "DAI", "TUSD", "PYUSD", "EURT", "EURR",
                "EURQ", "USDG", "USDS", "RLUSD", "USDR", "GUSD", "UST",
                "EURC", "USDQ", "EURI", "EUROP", "FDUSD", "USDP", "LUSD"}


def eur_pairs() -> dict[str, dict]:
    """Alle normale EUR-paren: symbool ('SOL/EUR') -> {pair, key, base}."""
    result = kraken._public("/0/public/AssetPairs")
    pairs: dict[str, dict] = {}
    for key, info in result.items():
        wsname = info.get("wsname")
        altname = info.get("altname", "")
        if not wsname or ".d" in altname:      # darkpool-paren overslaan
            continue
        base, _, quote = wsname.partition("/")
        if quote != "EUR":
            continue
        base = KRAKEN_BASE_ALIASES.get(base, base)
        pairs[f"{base}/EUR"] = {"pair": altname, "key": key, "base": base}
    return pairs


def momentum_score(candles) -> Optional[float]:
    """Score voor 'aan het stijgen / breakout nabij' op 1-uurs candles.

    0.6 x stijging laatste 24 uur + 0.4 x stijging laatste 48 uur,
    plus een kleine bonus als de koers binnen 3% van het 20-uurs-hoogte-
    punt zit. None bij te weinig historie."""
    closes = [c.close for c in candles]
    if len(closes) < 49 or closes[-25] <= 0 or closes[-49] <= 0:
        return None
    momentum_24h = closes[-1] / closes[-25] - 1.0
    momentum_48h = closes[-1] / closes[-49] - 1.0
    high_20 = max(c.high for c in candles[-21:-1])
    breakout_bonus = 0.02 if high_20 > 0 and closes[-1] >= 0.97 * high_20 else 0.0
    return 0.6 * momentum_24h + 0.4 * momentum_48h + breakout_bonus


def rank_by_eur_volume(pairs: dict[str, dict], tickers: dict,
                       min_eur_volume: float) -> list[tuple[float, str, str]]:
    """(eur_volume, symbool, kraken-paar) van hoog naar laag, gefilterd."""
    ranked = []
    for symbol, meta in pairs.items():
        if meta["base"] in STABLE_BASES:
            continue
        ticker = tickers.get(meta["key"])
        if not ticker:
            continue
        try:
            vwap_24h = float(ticker["p"][1])
            volume_24h = float(ticker["v"][1])
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        eur_volume = vwap_24h * volume_24h
        if eur_volume >= min_eur_volume:
            ranked.append((eur_volume, symbol, meta["pair"]))
    ranked.sort(reverse=True)
    return ranked


def scan_kraken(count: int, min_eur_volume: float, top_by_volume: int = 15,
                pause_s: float = 1.0, interval: str = "1h") -> list[str]:
    """Beste `count` stijgers onder de liquide Kraken-EUR-munten.

    `interval` is de candle-grootte van de handel; het momentum wordt in
    24/48 van die candles gemeten, zodat scanner en strategie dezelfde
    tijdshorizon delen."""
    pairs = eur_pairs()
    tickers = kraken._public("/0/public/Ticker")
    candidates = rank_by_eur_volume(pairs, tickers, min_eur_volume)
    log.info("crypto-scan: %d EUR-paren, %d liquide genoeg (>= EUR %.1fM/dag)",
             len(pairs), len(candidates), min_eur_volume / 1e6)
    scored: list[tuple[float, str]] = []
    for eur_volume, symbol, pair in candidates[:top_by_volume]:
        try:
            candles = kraken.fetch_candles(pair, interval, limit=80)
        except Exception as exc:
            log.warning("geen candles voor %s: %s", symbol, exc)
            continue
        score = momentum_score(candles)
        if score is not None and score > 0:      # alleen munten die stijgen
            scored.append((score, symbol))
        time.sleep(pause_s)                       # publieke API niet spammen
    scored.sort(reverse=True)
    picks = [symbol for _, symbol in scored[:count]]
    log.info("crypto-scan ranking: %s",
             ", ".join(f"{sym} ({score:+.1%})" for score, sym in scored) or "geen stijgers")
    return picks
