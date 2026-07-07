"""Telegram-meldingen: een berichtje op je telefoon bij elke trade of fout.

Instellen:
1. Praat op Telegram met @BotFather, stuur /newbot en volg de stappen → je krijgt een token.
2. Stuur je nieuwe bot een berichtje, open daarna
   https://api.telegram.org/bot<TOKEN>/getUpdates en lees je chat-id af.
3. Zet beide als environment variables:
   export TELEGRAM_BOT_TOKEN="123456:ABC..."
   export TELEGRAM_CHAT_ID="123456789"

Meldingen falen nooit hard: bij een fout wordt er alleen gelogd,
de bot handelt gewoon door.
"""

import logging
import os
import time

import requests

logger = logging.getLogger("trade_bot")


class TelegramNotifier:
    ERROR_COOLDOWN = 900  # max één foutmelding per 15 minuten (tegen spam)

    def __init__(self, token: str, chat_id: str, timeout: int = 10):
        if not token or not chat_id:
            raise ValueError("token en chat_id zijn verplicht")
        self.url = f"https://api.telegram.org/bot{token}/sendMessage"
        self.chat_id = chat_id
        self.timeout = timeout
        self._last_error_sent = -float("inf")  # eerste foutmelding altijd doorlaten

    @classmethod
    def from_env(cls) -> "TelegramNotifier | None":
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
        if token and chat_id:
            return cls(token, chat_id)
        return None

    def send(self, text: str) -> bool:
        try:
            resp = requests.post(self.url, json={"chat_id": self.chat_id, "text": text},
                                 timeout=self.timeout)
            if resp.status_code != 200:
                logger.warning("Telegram-melding mislukt (%d): %s",
                               resp.status_code, resp.text[:200])
                return False
            return True
        except requests.RequestException as exc:
            logger.warning("Telegram-melding mislukt: %s", exc)
            return False

    def send_error(self, text: str) -> bool:
        """Als send(), maar met een afkoelperiode zodat een storing niet spamt."""
        now = time.monotonic()
        if now - self._last_error_sent < self.ERROR_COOLDOWN:
            return False
        self._last_error_sent = now
        return self.send(text)
