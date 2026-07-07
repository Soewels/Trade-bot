# Trade-bot

Een crypto trading bot in Python met **paper trading** (gesimuleerd geld) en **backtesting**.
Koersdata komt van de publieke Binance API — er is **geen API-key nodig** en er wordt
**nooit met echt geld gehandeld**.

## Functies

- 📈 **Drie strategieën**: SMA-crossover (trendvolgend), RSI (mean-reversion) en MACD
- ⚖️ **Vergelijken**: `compare` draait alle strategieën op dezelfde data en zet ze naast elkaar
- 🧪 **Backtester**: test een strategie op historische data met rendement, winrate en max drawdown
- 📝 **Paper trading**: live bot-loop die orders simuleert met een virtueel portfolio
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
├── portfolio.py   # paper-trading portfolio met risicobeheer
├── backtest.py    # backtester met statistieken
└── bot.py         # live paper-trading loop
main.py            # command-line interface
tests/             # unit tests
```

## ⚠️ Disclaimer

Deze bot is bedoeld voor **educatie en simulatie**. Hij plaatst geen echte orders.
Resultaten uit backtests zijn geen garantie voor toekomstig rendement. Wil je dit
ooit uitbreiden naar echt handelen, doe dat dan volledig op eigen risico en begin
altijd met kleine bedragen.
