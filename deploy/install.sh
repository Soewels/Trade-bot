#!/usr/bin/env bash
# Installeer de trade bot als systemd-service op een verse Ubuntu/Debian-server.
# Gebruik:  sudo bash deploy/install.sh
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Draai dit script als root:  sudo bash deploy/install.sh" >&2
  exit 1
fi

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "==> Pakketten installeren"
apt-get update -q
apt-get install -yq python3 python3-pip git

echo "==> Gebruiker 'tradebot' aanmaken (zonder inlogrechten)"
id tradebot >/dev/null 2>&1 || useradd --system --home /opt/trade-bot --shell /usr/sbin/nologin tradebot

echo "==> Code naar /opt/trade-bot kopiëren"
mkdir -p /opt/trade-bot
cp -r "$REPO_DIR"/main.py "$REPO_DIR"/trade_bot "$REPO_DIR"/requirements.txt /opt/trade-bot/
chown -R tradebot:tradebot /opt/trade-bot

echo "==> Python-dependencies installeren"
pip3 install -q -r /opt/trade-bot/requirements.txt

echo "==> Configuratiebestand klaarzetten"
if [ ! -f /etc/trade-bot.env ]; then
  cp "$REPO_DIR"/deploy/trade-bot.env.example /etc/trade-bot.env
  chmod 600 /etc/trade-bot.env
  echo "    /etc/trade-bot.env aangemaakt — VUL EERST JE API-KEYS IN:"
  echo "    sudo nano /etc/trade-bot.env"
else
  echo "    /etc/trade-bot.env bestaat al, laat ik staan."
fi

echo "==> systemd-service installeren"
cp "$REPO_DIR"/deploy/trade-bot.service /etc/systemd/system/trade-bot.service
systemctl daemon-reload
systemctl enable trade-bot

cat <<'EOF'

Klaar! Nu nog:
  1. sudo nano /etc/trade-bot.env      # API-keys invullen
  2. sudo systemctl start trade-bot    # bot starten
  3. sudo journalctl -u trade-bot -f   # logs bekijken (Ctrl+C om te stoppen met kijken)

De bot start voortaan automatisch na een reboot en herstart zichzelf na een crash.
EOF
