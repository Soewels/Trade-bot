# Trade-bot

Een crypto trading bot in Python met **backtesting**, **paper trading** (gesimuleerd geld)
en optioneel **echt handelen** via Binance (testnet of live). Standaard draait alles in
paper-modus: geen API-key nodig, geen echt geld.

## Functies

- 📈 **Drie strategieën**: SMA-crossover (trendvolgend), RSI (mean-reversion) en MACD
- ⚖️ **Vergelijken**: `compare` draait alle strategieën op dezelfde data en zet ze naast elkaar
- 🧪 **Backtester**: test een strategie op historische data met rendement, winrate en max drawdown
- 📝 **Paper trading**: live bot-loop die orders simuleert met een virtueel portfolio
- 💸 **Echt handelen** (optioneel): market orders via de Binance API, eerst op het gratis
  testnet, daarna live — met bestedingslimiet per order en expliciete bevestiging
- 📱 **Mobiel dashboard**: volg de bot op je telefoon, met pauze- en noodstopknop
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
| `--strategy` | `sma_cross` | `sma_cross`, `rsi` of `macd` |
| `--fast` / `--slow` | 10 / 30 | SMA-perioden voor de crossover |
| `--rsi-period` | 14 | RSI-periode |
| `--oversold` / `--overbought` | 30 / 70 | RSI-drempels |
| `--macd-fast` / `--macd-slow` / `--macd-signal` | 12 / 26 / 9 | MACD-perioden |
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

## Tests draaien

```bash
python -m unittest discover -s tests -v
```

## Projectstructuur

```
trade_bot/
├── config.py      # instellingen (BotConfig)
├── data.py        # Binance publieke API + CSV-loader
├── indicators.py  # SMA, EMA, RSI, MACD
├── strategy.py    # SMA-crossover, RSI- en MACD-strategie
├── portfolio.py   # portfolio-boekhouding met risicobeheer
├── exchange.py    # Binance-koppeling voor echte orders (testnet/live)
├── backtest.py    # backtester met statistieken
├── webapp.py      # mobiel dashboard (webserver)
└── bot.py         # trading-loop (paper, testnet of live)
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
