# Trade-bot

Een crypto trading bot in Python met **backtesting**, **paper trading** (gesimuleerd geld)
en optioneel **echt handelen** via **Binance** of **Kraken**. Standaard draait alles in
paper-modus: geen API-key nodig, geen echt geld.

> 🆕 **Nieuw**: er is nu ook een aparte **multi-instrument bot** in [`bot/`](bot/)
> die vijf instrumenten tegelijk handelt met drie strategieën en ATR-gebaseerd
> risicobeheer. Standaard draait hij op **Europese beurzen in euro's** (UCITS-ETF's
> op Xetra via Interactive Brokers + BTC/EUR via Kraken); met `BOT_MARKET=us` op
> de Amerikaanse markt via Alpaca — zie [`bot/README.md`](bot/README.md).
> Starten: `python -m bot.main` (instellingen in `.env`, zie `.env.example`).

## Functies

- 📈 **Vijf strategieën**: SMA-crossover en Breakout (trendvolgend), RSI en
  Bollinger Bands (mean-reversion), en MACD (momentum)
- ⚖️ **Vergelijken**: `compare` draait alle strategieën op dezelfde data en zet ze naast elkaar
- 🧪 **Backtester**: test een strategie op historische data met rendement, winrate en max drawdown
- 📝 **Paper trading**: live bot-loop die orders simuleert met een virtueel portfolio
- 💸 **Echt handelen** (optioneel): market orders via de Binance API, eerst op het gratis
  testnet, daarna live — met bestedingslimiet per order en expliciete bevestiging
- 📱 **Mobiel dashboard**: volg de bot op je telefoon, met pauze- en noodstopknop
- 🧠 **Zelflerend** (`--strategy auto`): test elke paar uur alle strategieën op recente
  marktdata en schakelt automatisch naar wat nu het beste werkt
- 💾 **Herstart-veilig**: de bot onthoudt zijn positie en trades, ook na een crash of reboot
- 🔔 **Telegram-meldingen**: berichtje op je telefoon bij elke koop, verkoop of fout
- 🖥️ **Server-klaar**: installatiescript + systemd-service voor 24/7 draaien op een VPS
- 🛡️ **Risicobeheer**: stop-loss en take-profit per positie, handelskosten worden meegerekend
- 📂 **CSV-support**: backtest ook op eigen data (`timestamp,open,high,low,close,volume`)

## Installatie

```bash
pip install -r requirements.txt
```

## Gebruik

### Backtest (aanrader om mee te beginnen)

```bash
# SMA-crossover op BTC/USDT, 1-uurs candles
python main.py backtest --symbol BTCUSDT --interval 1h --strategy sma_cross

# RSI-strategie op ETH/USDT, met individuele trades in de output
python main.py backtest --symbol ETHUSDT --interval 4h --strategy rsi --trades

# Backtest op eigen CSV-data (of de meegeleverde voorbeelddata, werkt offline)
python main.py backtest --csv examples/sample_data.csv --strategy sma_cross
```

### Strategieën vergelijken

```bash
# alle strategieën op dezelfde data, gesorteerd op rendement
python main.py compare --symbol BTCUSDT --interval 1h
python main.py compare --csv examples/sample_data.csv
```

### Live paper trading

```bash
python main.py run --symbol BTCUSDT --interval 15m --poll 60
```

De bot haalt elke `--poll` seconden nieuwe data op, bepaalt een signaal en
simuleert orders. Stoppen met `Ctrl+C`.

### Echt handelen — stap 1: oefenen op het testnet (nepgeld)

1. Maak gratis testnet-keys aan op <https://testnet.binance.vision> (inloggen met GitHub).
2. Zet de keys als environment variables en start met `--testnet`:

```bash
export BINANCE_API_KEY="je_testnet_key"
export BINANCE_API_SECRET="je_testnet_secret"
python main.py run --symbol BTCUSDT --interval 15m --testnet
```

De bot plaatst nu échte orders, maar met nepgeld. Laat dit minstens een paar weken
draaien voordat je aan live denkt.

### Echt handelen — stap 2: live (echt geld, eigen risico)

1. Maak API-keys aan op Binance met **alleen spot-handel** rechten — zet opnames
   (withdrawals) **uit** en beperk de key tot je eigen IP-adres.
2. Start met `--live`; de bot toont de instellingen en vraagt om bevestiging:

```bash
export BINANCE_API_KEY="je_echte_key"
export BINANCE_API_SECRET="je_echte_secret"
python main.py run --symbol BTCUSDT --interval 1h --live --max-order 25 --cash 100
```

Veiligheidsgrendels in live-modus:

- `--max-order` (standaard 100): maximum bedrag per aankoop, wat er ook gebeurt
- `--cash`: totaalbudget — de bot gebruikt nooit meer dan dit, ook niet als je
  exchange-saldo hoger is
- expliciete bevestiging bij het starten (je moet het handelspaar intypen)
- stop-loss en take-profit blijven actief zoals in paper-modus

Begin klein: `--max-order 25 --cash 100` betekent maximaal €100 aan totale blootstelling.

### Kraken gebruiken

Alle commando's werken ook op Kraken met `--exchange kraken`. Voor Nederlandse
gebruikers is dat vaak de logischere keuze: Binance bedient Nederland niet meer,
Kraken wel (met EU/MiCA-licentie).

```bash
# koersdata en paper trading via Kraken (geen account nodig)
python main.py compare --exchange kraken --symbol XBTEUR --interval 4h
python main.py run --exchange kraken --symbol XBTEUR --interval 15m --web
```

Let op de symboolnamen: Kraken noemt Bitcoin **XBT** — dus `XBTUSD`, `XBTEUR`,
`ETHEUR`, enz. Vergeet je het aan te passen, dan kiest de bot automatisch `XBTUSD`.

Voor echt handelen: maak API-keys aan via Kraken → Settings → API met alleen
*Query Funds* en *Create & Modify Orders* rechten (géén withdrawals), en zet ze
als `KRAKEN_API_KEY` en `KRAKEN_API_SECRET`. **Kraken heeft geen testnet** voor
spot-handel; het oefentraject is daar dus: eerst weken paper trading, daarna
`--live` met een klein bedrag en een lage `--max-order`.

### Zelflerende modus

```bash
python main.py run --symbol BTCUSDT --interval 15m --strategy auto
```

Met `--strategy auto` kiest de bot bij de start zelf de beste strategie en evalueert hij
elke 6 uur opnieuw (instelbaar met `--relearn-hours`): hij backtest alle strategieën op de
laatste ~500 candles en schakelt — alleen als hij geen open positie heeft — over naar de
winnaar. Elke wissel wordt gelogd en (indien ingesteld) via Telegram gemeld.

Eerlijke kanttekening: dit is aanpassen aan de recente markt, geen glazen bol. Het
voorkomt dat je met een trendstrategie in een zijwaartse markt blijft hangen, maar het
garandeert geen winst.

### Herstart-veilig geheugen

De bot bewaart zijn toestand (positie, instapprijs, trades) standaard in `bot_state.json`
en leest die bij het opstarten terug. Crasht de server of herstart je de bot, dan weet hij
dus nog precies wat hij in bezit heeft en blijft de stop-loss werken. Ander pad instellen:
`--state /pad/naar/state.json`; uitzetten: `--state ""`. In live-modus is het echte
exchange-saldo altijd leidend voor de cash; alleen positie en instapprijs komen uit het
bestand.

### Telegram-meldingen

1. Praat op Telegram met **@BotFather**, stuur `/newbot` en volg de stappen → je krijgt een token.
2. Stuur je nieuwe bot een berichtje en open `https://api.telegram.org/bot<TOKEN>/getUpdates`
   in je browser → lees je `chat.id` af.
3. Zet beide als environment variables en start de bot gewoon:

```bash
export TELEGRAM_BOT_TOKEN="123456:ABC..."
export TELEGRAM_CHAT_ID="123456789"
python main.py run --symbol BTCUSDT --interval 15m
```

Je krijgt nu een berichtje bij elke (paper of echte) koop en verkoop — met reden en
resultaat — bij strategiewissels in auto-modus, en maximaal één per kwartier bij storingen.

### 24/7 draaien op een server (VPS)

Op een verse Ubuntu/Debian-server (bv. Hetzner, ~€4/maand):

```bash
git clone https://github.com/Soewels/Trade-bot.git
cd Trade-bot
sudo bash deploy/install.sh
sudo nano /etc/trade-bot.env       # API-keys en instellingen invullen
sudo systemctl start trade-bot
sudo journalctl -u trade-bot -f    # live meekijken met de logs
```

De bot start daarna automatisch bij elke reboot en herstart zichzelf na een crash —
in combinatie met het herstart-veilige geheugen verliest hij daarbij nooit zijn positie.
Voor het dashboard op afstand: installeer [Tailscale](https://tailscale.com) op de server
en je telefoon, dan bereik je `http://<tailscale-ip>:8080` veilig vanaf overal.

### Dashboard op je telefoon

Start de bot met `--web` erbij (werkt in paper-, testnet- en live-modus):

```bash
python main.py run --symbol BTCUSDT --interval 15m --web
```

De terminal toont dan iets als:

```
📱 Dashboard voor op je telefoon (zelfde wifi-netwerk):
   http://192.168.1.23:8080
   Toegangscode voor de knoppen: a1b2c3
```

Open dat adres in de browser van je telefoon (telefoon en computer moeten op
hetzelfde wifi-netwerk zitten) en kies **"Toevoegen aan beginscherm"** — dan
staat de bot als app-icoon op je telefoon. Je ziet live je totale waarde,
rendement, positie en trades, en je kunt de bot **pauzeren** of met de
**noodstop** alles direct verkopen. Voor die knoppen is de toegangscode uit de
terminal nodig; meekijken kan zonder code.

Draai je de bot op een VPS in plaats van thuis, gebruik dan een SSH-tunnel
(`ssh -L 8080:localhost:8080 gebruiker@server`) of een dienst als Tailscale —
zet het dashboard niet onbeschermd open op het publieke internet.

### Huidige prijs

```bash
python main.py price --symbol BTCUSDT
```

## Belangrijkste opties

| Optie | Standaard | Betekenis |
|---|---|---|
| `--exchange` | `binance` | `binance` of `kraken` |
| `--strategy` | `sma_cross` | `sma_cross`, `rsi`, `macd`, `bollinger`, `breakout` of `auto` |
| `--relearn-hours` | 6 | bij `auto`: elke zoveel uur opnieuw evalueren |
| `--state` | `bot_state.json` | toestandsbestand voor herstart-veiligheid (`""` = uit) |
| `--fast` / `--slow` | 10 / 30 | SMA-perioden voor de crossover |
| `--rsi-period` | 14 | RSI-periode |
| `--oversold` / `--overbought` | 30 / 70 | RSI-drempels |
| `--macd-fast` / `--macd-slow` / `--macd-signal` | 12 / 26 / 9 | MACD-perioden |
| `--bb-period` / `--bb-std` | 20 / 2.0 | Bollinger Bands-instellingen |
| `--breakout-period` | 20 | uitbraakperiode |
| `--cash` | 10000 | virtueel startkapitaal (USDT) |
| `--size` | 0.95 | fractie van cash per aankoop |
| `--fee` | 0.001 | handelskosten per order (0.1%) |
| `--stop-loss` | 0.05 | verkoop bij 5% verlies (0 = uit) |
| `--take-profit` | 0.15 | verkoop bij 15% winst (0 = uit) |
| `--testnet` | uit | echte orders op het Binance-testnet (nepgeld) |
| `--live` | uit | echte orders met echt geld (vraagt bevestiging) |
| `--max-order` | 100 | max bedrag per live aankoop |
| `--web` | uit | mobiel dashboard op poort 8080 (of `--web 9000`) |

## Hoe werken de strategieën?

- **SMA-crossover**: koopt wanneer het snelle voortschrijdend gemiddelde omhoog kruist
  door het trage (golden cross), verkoopt bij een kruising omlaag (death cross).
- **RSI**: koopt wanneer de RSI vanuit oversold (< 30) weer omhoog kruist,
  verkoopt wanneer de RSI vanuit overbought (> 70) weer omlaag kruist.
- **MACD**: koopt wanneer de MACD-lijn omhoog kruist door de signaallijn,
  verkoopt bij een kruising omlaag.
- **Bollinger Bands**: koopt wanneer de koers vanuit de onderband (2 standaard-
  deviaties onder het gemiddelde) weer omhoog kruist, verkoopt vanuit de bovenband.
- **Breakout**: koopt wanneer de koers uitbreekt boven het hoogste punt van de
  laatste 20 candles, verkoopt bij een val onder het laagste punt.

## Tests draaien

```bash
python -m unittest discover -s tests -v
```

## Projectstructuur

```
trade_bot/
├── config.py      # instellingen (BotConfig)
├── data.py        # Binance publieke API + CSV-loader
├── kraken.py      # Kraken: koersdata + echte orders
├── market.py      # exchange-keuze voor koersdata
├── indicators.py  # SMA, EMA, RSI, MACD
├── strategy.py    # SMA-crossover, RSI- en MACD-strategie
├── portfolio.py   # portfolio-boekhouding met risicobeheer
├── exchange.py    # Binance-orders (testnet/live) + gedeelde types
├── backtest.py    # backtester met statistieken
├── webapp.py      # mobiel dashboard (webserver)
├── state.py       # toestand opslaan/herstellen (herstart-veilig)
├── notify.py      # Telegram-meldingen
└── bot.py         # trading-loop (paper, testnet of live) + zelflerende modus
deploy/            # installatiescript en systemd-service voor een VPS
main.py            # command-line interface
tests/             # unit tests
```

## ⚠️ Disclaimer

Handelen in crypto is risicovol; je kunt (al) je inleg verliezen. Deze bot en zijn
strategieën bieden **geen enkele garantie op winst** — resultaten uit backtests zeggen
weinig over de toekomst. Live handelen is volledig op eigen risico. Vuistregels:

- Oefen eerst weken op paper/testnet voordat je live gaat.
- Handel alleen met geld dat je kunt missen, en begin met kleine bedragen.
- Geef je API-key nooit opname-rechten en deel hem met niemand.
- Een bot is geen "geld verdienen zonder iets te doen" — controleer hem dagelijks.
