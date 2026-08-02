"""Wereldwijde aandelen-screener.

De bot vindt zijn eigen aandelen: de IBKR-marktscanner levert per regio
(VS, Europa, Azië — instelbaar via STOCK_REGIONS) de meest actieve
aandelen, die vervolgens worden gerangschikt op gemiddelde dagelijkse
omzet **omgerekend naar euro's** (koers x volume x wisselkoers), zodat
een Duits, Amerikaans of Japans aandeel eerlijk vergeleken wordt. De top
`count` aandelen gaan de mean-reversion strategie in — long en, met een
margin-account, short. Herscreening gebeurt elke US_STOCK_RESCAN_DAYS;
aandelen met een open positie worden nooit weggeruild.

Veiligheidsregels: kandidaten in Britse ponden worden overgeslagen (LSE
noteert in pence — dat verstoort de positiegrootte 100x) en aandelen op
een beurs waarvan we de openingstijden niet kennen ook.
"""

import logging

from .brokers.ibkr_broker import hours_for_primary_exchange
from .models import Bar

log = logging.getLogger("alpaca_bot.screener")

# Fallback-metadata (Amerikaans aandeel in USD); nieuwe kandidaten krijgen
# hun eigen metadata uit de scanner.
US_STOCK_META = {"exchange": "SMART", "currency": "USD", "hours": "US"}


def rank_by_dollar_volume(bars_by_symbol: dict[str, list[Bar]],
                          min_dollar_volume: float,
                          lookback: int = 20,
                          fx: dict[str, float] | None = None) -> list[str]:
    """Symbolen op gemiddelde dagelijkse omzet (basisvaluta), hoogste eerst.
    `fx` rekent lokale valuta om naar de basisvaluta (ontbreekt = 1.0).
    Symbolen onder `min_dollar_volume` vallen af."""
    scores: dict[str, float] = {}
    for symbol, bars in bars_by_symbol.items():
        if not bars:
            continue
        window = bars[-lookback:]
        avg = sum(b.close * b.volume for b in window) / len(window)
        avg *= (fx or {}).get(symbol, 1.0)
        if avg >= min_dollar_volume:
            scores[symbol] = avg
    return sorted(scores, key=scores.__getitem__, reverse=True)


def merge_universe(current: list[str], held: set[str], picks: list[str],
                   count: int) -> list[str]:
    """Nieuw universum na een herscreening: symbolen met een open positie
    blijven altijd staan; vrije plekken worden gevuld met de beste picks."""
    universe = [sym for sym in current if sym in held]
    for sym in picks:
        if len(universe) >= max(count, len(universe)):
            break
        if sym not in universe:
            universe.append(sym)
    return universe


def find_liquid_stocks(broker, count: int, min_price: float,
                       min_market_cap_musd: float, min_dollar_volume: float,
                       locations: list[str]) -> tuple[list[str], dict[str, dict]]:
    """Scan de regio's en rangschik op euro-omzet.

    Geeft (ranking, metadata per symbool) terug; de metadata (valuta,
    primaire beurs, openingstijden) is nodig om het aandeel correct te
    verhandelen en wordt door de bot in de state bewaard."""
    rows = max(count * 3, 10)
    candidates = broker.scan_most_active(min_price, min_market_cap_musd,
                                         rows, tuple(locations))
    log.info("scanner: %d kandidaten uit %d regio-locaties",
             len(candidates), len(locations))
    bars_by_symbol: dict[str, list[Bar]] = {}
    fx_by_symbol: dict[str, float] = {}
    metas: dict[str, dict] = {}
    for cand in candidates:
        symbol = cand["symbol"]
        currency = cand.get("currency") or "USD"
        primary = cand.get("primaryExchange") or ""
        if currency == "GBP":
            log.info("kandidaat %s overgeslagen: GBP/pence-notering", symbol)
            continue
        hours = hours_for_primary_exchange(primary)
        if hours is None:
            log.info("kandidaat %s overgeslagen: onbekende beurs %s",
                     symbol, primary)
            continue
        meta = {"exchange": "SMART", "currency": currency,
                "primaryExchange": primary, "hours": hours}
        broker.add_instrument(symbol, meta)
        try:
            fx_by_symbol[symbol] = broker.to_base_rate(symbol)
            bars_by_symbol[symbol] = broker.fetch_bars(symbol, 1440, 30)
            metas[symbol] = meta
        except Exception as exc:
            log.warning("kandidaat %s overgeslagen (%s)", symbol, exc)
    ranked = rank_by_dollar_volume(bars_by_symbol, min_dollar_volume,
                                   fx=fx_by_symbol)
    log.info("liquiditeitsranking (>= EUR %.0fM omzet/dag): %s",
             min_dollar_volume / 1e6, ", ".join(ranked) or "geen")
    return ranked, metas
