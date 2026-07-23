#!/usr/bin/env bash
# Installeer de multi-instrument bot (bot/, EU-profiel) als systemd-service,
# inclusief een headless IB Gateway in Docker. Draait prima naast de
# bestaande trade-bot service op dezelfde server.
# Gebruik:  sudo bash deploy/multibot-install.sh
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Draai dit script als root:  sudo bash deploy/multibot-install.sh" >&2
  exit 1
fi

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "==> Pakketten installeren"
apt-get update -q
apt-get install -yq python3 python3-venv python3-pip git docker.io docker-compose-v2

echo "==> Gebruiker 'tradebot' aanmaken (zonder inlogrechten)"
id tradebot >/dev/null 2>&1 || useradd --system --home /opt/multi-bot --shell /usr/sbin/nologin tradebot

echo "==> Code naar /opt/multi-bot kopiëren"
mkdir -p /opt/multi-bot
# trade_bot/ is nodig voor de Kraken-module die de nieuwe bot hergebruikt
cp -r "$REPO_DIR"/bot "$REPO_DIR"/trade_bot "$REPO_DIR"/config.py /opt/multi-bot/
mkdir -p /opt/multi-bot/ib-gateway
cp "$REPO_DIR"/deploy/ib-gateway/docker-compose.yml /opt/multi-bot/ib-gateway/

echo "==> Python-omgeving (venv) aanmaken"
# Eigen venv i.p.v. systeem-pip: Ubuntu 24.04+ blokkeert systeembrede
# pip-installaties (PEP 668), en zo botsen we ook niet met de andere bot.
if [ ! -d /opt/multi-bot/venv ]; then
  python3 -m venv /opt/multi-bot/venv
fi
/opt/multi-bot/venv/bin/pip install --quiet --upgrade pip
/opt/multi-bot/venv/bin/pip install --quiet "requests>=2.28" "ib_async>=1.0"
chown -R tradebot:tradebot /opt/multi-bot

echo "==> Configuratiebestand klaarzetten"
if [ ! -f /etc/multi-bot.env ]; then
  cp "$REPO_DIR"/deploy/multi-bot.env.example /etc/multi-bot.env
  chmod 600 /etc/multi-bot.env
  echo "    /etc/multi-bot.env aangemaakt — VUL EERST JE GEGEVENS IN:"
  echo "    sudo nano /etc/multi-bot.env"
else
  echo "    /etc/multi-bot.env bestaat al, laat ik staan."
fi

echo "==> systemd-service installeren"
cp "$REPO_DIR"/deploy/multi-bot.service /etc/systemd/system/multi-bot.service
systemctl daemon-reload
systemctl enable multi-bot
systemctl enable --now docker

cat <<'EOF'

Klaar! Nu nog, in deze volgorde:
  1. sudo nano /etc/multi-bot.env                    # IBKR-login (paper!) e.d. invullen
  2. cd /opt/multi-bot/ib-gateway && docker compose up -d   # IB Gateway starten
     docker compose logs -f                          # wachten tot de login gelukt is
  3. sudo systemctl start multi-bot                  # bot starten
  4. sudo journalctl -u multi-bot -f                 # logs bekijken

Beide starten voortaan automatisch na een reboot en herstarten zichzelf na
een crash; de bot pakt zijn posities daarbij weer op uit de state-bestanden.
De bestaande trade-bot service wordt door dit script niet aangeraakt.
EOF
