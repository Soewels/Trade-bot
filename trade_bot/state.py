"""Toestand opslaan en herstellen, zodat de bot een herstart overleeft.

Zonder dit zou de bot na een crash of reboot zijn open positie 'vergeten':
de coins staan dan nog op de exchange, maar de stop-loss werkt niet meer.
De toestand wordt na elke trade en elke stap weggeschreven naar een JSON-bestand.
"""

import json
import logging
import os
from datetime import datetime

from .portfolio import Trade

logger = logging.getLogger("trade_bot")

STATE_VERSION = 1


def save_state(bot, path: str) -> None:
    """Schrijf de bot-toestand atomair weg (tmp-bestand + rename)."""
    p = bot.portfolio
    data = {
        "version": STATE_VERSION,
        "saved_at": datetime.now().astimezone().isoformat(),
        "symbol": bot.config.symbol,
        "mode": bot.mode,
        "active_strategy": bot.active_strategy,
        "relearn_scores": bot.last_relearn_scores,
        "cash": p.cash,
        "position": p.position,
        "entry_price": p.entry_price,
        "equity_history": [round(v, 2) for v in bot.equity_history],
        "trades": [{
            "timestamp": t.timestamp.isoformat(),
            "side": t.side, "price": t.price, "quantity": t.quantity,
            "fee": t.fee, "reason": t.reason,
        } for t in p.trades],
    }
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def load_state(bot, path: str) -> bool:
    """Herstel de bot-toestand. Geeft True terug als er iets is hersteld."""
    if not os.path.exists(path):
        return False
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("toestandsbestand %s onleesbaar, start schoon: %s", path, exc)
        return False

    if data.get("symbol") != bot.config.symbol:
        logger.warning("toestandsbestand is voor %s, bot draait %s — start schoon "
                       "(verwijder %s of gebruik een ander --state pad)",
                       data.get("symbol"), bot.config.symbol, path)
        return False

    p = bot.portfolio
    if not bot.live:
        # In live-modus is het echte exchange-saldo leidend, niet het bestand.
        p.cash = float(data.get("cash", p.cash))
    p.position = float(data.get("position", 0.0))
    p.entry_price = float(data.get("entry_price", 0.0))
    p.trades = [Trade(
        timestamp=datetime.fromisoformat(t["timestamp"]),
        side=t["side"], price=float(t["price"]), quantity=float(t["quantity"]),
        fee=float(t.get("fee", 0.0)), reason=t.get("reason", "signal"),
    ) for t in data.get("trades", [])]
    bot.equity_history.extend(float(v) for v in data.get("equity_history", []))

    if bot.config.strategy == "auto" and data.get("active_strategy"):
        bot.set_active_strategy(data["active_strategy"])
    if isinstance(data.get("relearn_scores"), dict):
        bot.last_relearn_scores = {str(k): float(v)
                                   for k, v in data["relearn_scores"].items()}

    logger.info("toestand hersteld uit %s: positie=%.8f, instap=%.2f, %d trades "
                "(opgeslagen %s)", path, p.position, p.entry_price,
                len(p.trades), data.get("saved_at", "?"))
    return True
