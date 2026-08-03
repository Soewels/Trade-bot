# Multi-instrument trading bot (EU & US)

A Python trading bot that trades **5 instruments simultaneously** with three
independent strategy modules and strict ATR-based risk management. It ships
with two market profiles, selected via `BOT_MARKET` in `.env`:

| | `eu` (default) | `us` |
|---|---|---|
| S&P 500 | **VUSA** — Vanguard S&P 500 UCITS (Amsterdam, EUR) | SPY |
| Nasdaq 100 | **CNDX** — iShares Nasdaq 100 UCITS (London, USD) | QQQ |
| Crypto | self-screened risers via Kraken (EUR) | BTC/USD via Alpaca |
| Gold | **SGLD** — Invesco Physical Gold (London, USD) | GLD |
| Oil | **CRUD** — WisdomTree WTI Crude Oil (London, USD) | USO |
| Broker | Interactive Brokers + Kraken | Alpaca |
| Base currency | EUR (USD listings FX-converted live) | USD |
| P&L calendar | Europe/Amsterdam | America/New_York |

The EU listings were chosen empirically with `deploy/ibkr_datatest.py`:
Euronext Amsterdam and the LSE serve **free delayed data** through IBKR,
while Xetra and Euronext Paris require a paid data subscription.

The **EU profile is PRIIPs-proof**: it uses UCITS ETFs/ETCs that European
retail investors are actually allowed to buy, on European exchanges, in
euros, via an EU-regulated broker (Interactive Brokers Ireland for Dutch
customers) — no US-domiciled ETFs anywhere.

> This bot lives next to the original crypto bot in this repo (`trade_bot/`,
> `main.py`); the two are completely independent.

## Strategies

| Module | Instruments | Candles | Logic |
|---|---|---|---|
| `strategies/mean_reversion.py` | SXR8/SPY, SXRV/QQQ | 15 min | Long/short when price is > 1.5σ (S&P) / 1.8σ (Nasdaq) away from the 20-period SMA; exit when price returns to the mean. |
| `strategies/momentum_breakout.py` | BTC/EUR or BTC/USD | 1 hour | Long on a break above the 20-period high with volume ≥ 1.5× the 20-period average; a confirmed break below the 20-period low exits the long (no crypto shorts on spot). Trailing stop: 2× ATR. |
| `strategies/trend_following.py` | 4GLD/GLD, OD7F/USO | 4 hours | Long when the 50 EMA crosses above the 200 EMA; short (or exit) on the cross down. Trailing stop: 3× ATR. |

## Risk management (`risk_manager.py`)

- **ATR position sizing**: 14-period ATR per instrument, sized so a 1 ATR
  adverse move equals exactly **1% of combined account equity**. Quiet
  instruments get bigger positions, volatile ones smaller — money at risk
  stays constant.
- **Hard stop**: every position has a stop 1 ATR from the fill price, i.e.
  1% of equity. No exceptions. Stops are checked every 30 seconds.
- **Trailing stops** (breakout and trend strategies) ratchet with the best
  price seen and only ever tighten.
- **Correlation filter**: if both equity index trackers (SXR8 **and** SXRV,
  or SPY **and** QQQ) are long, no new Bitcoin longs are opened — no
  tripling up on risk-on exposure.
- Position notional is additionally capped at 95% of available buying power.
- **Separate budgets** (optional): `CRYPTO_BUDGET` and `STOCKS_BUDGET` in
  `.env` cap the total open position value per category (in your base
  currency; `0` = unlimited). New entries are shrunk to fit the remaining
  room, or skipped when the budget is full.

## Setup (EU profile)

1. **Install dependencies**

   ```bash
   pip install -r requirements.txt
   cp .env.example .env
   ```

2. **Interactive Brokers** (for the four ETFs/ETCs)
   - Open an account at [interactivebrokers.ie](https://www.interactivebrokers.ie)
     (EU-regulated; every account automatically includes a **paper account**).
   - Install [TWS or IB Gateway](https://www.interactivebrokers.com/en/trading/ibgateway-stable.php),
     log in with your **paper** credentials first, and enable
     *API → Settings → Enable ActiveX and Socket Clients*.
   - Default `.env` expects TWS paper on port 7497 (IB Gateway paper: 4002).
   - The bot requests **delayed market data** (works without paid data
     subscriptions); with a data subscription it uses real-time quotes
     automatically. For live trading later you'll want the (cheap) Xetra
     data subscription.

3. **Kraken** (for BTC/EUR)
   - Without API keys the BTC leg runs in **paper mode**: real Kraken
     prices, simulated fills against a virtual balance (`KRAKEN_PAPER_CASH`,
     default €10.000) — Kraken has no spot testnet.
   - For live: create keys with only *Query Funds* and *Create & Modify
     Orders* permissions (no withdrawals) and put them in `.env`.

4. **Run**

   ```bash
   python -m bot.main
   ```

### Choosing different EU listings

The defaults are all on Xetra (IBKR exchange code `IBIS`) in EUR. Prefer
e.g. CSPX on Euronext Amsterdam? Change the symbol/exchange in the
`MARKETS["eu"]["instruments"]` table in `config.py` (and the matching
strategy symbol lists) — verify the ticker/exchange combination in TWS
first. Exchange opening hours per IBKR code are configured in
`bot/brokers/ibkr_broker.py` (`EXCHANGE_HOURS`).

### Self-screened stocks, worldwide (long & short)

Individual **stocks** are PRIIPs-exempt (unlike US ETFs), so EU retail can
trade them freely — and they're the easiest instruments to short. The bot
finds them itself, across the regions in `STOCK_REGIONS` (default
`US,EU,ASIA`):

1. at startup (and weekly, `US_STOCK_RESCAN_DAYS`) it asks the **IBKR
   market scanner** per region for the most active stocks above
   `US_STOCK_MIN_PRICE` and a $10B market cap;
2. it ranks all candidates on **average daily turnover converted to EUR**
   (price × volume × live FX) computed from real daily bars, keeping those
   above `US_STOCK_MIN_DOLLAR_VOLUME` (€50M/day by default) — so a German,
   American or Japanese stock competes fairly;
3. the top `US_STOCK_COUNT` (default 3, `0` = off) join the mean-reversion
   strategy with a wider 2.0σ entry band, long and — with
   `IBKR_ALLOW_SHORTS=1` and a margin account — short.

Each stock trades during **its own exchange's hours** (New York, Xetra,
Hong Kong, Tokyo, …) and its currency/exchange metadata survives restarts.
Safety rules: London-listed candidates are skipped (pence quotation would
distort sizing 100×) and so is any exchange whose opening hours the bot
doesn't know. On a rescan, stocks with an **open position are never
swapped out**. Position sizing converts the local-currency ATR to EUR at
the live exchange rate, so the 1%-of-equity risk rule holds exactly.

### Self-screened crypto (Kraken)

By default the bot also picks its own coins (`CRYPTO_AUTO_COUNT=5`, `0` =
off): every `CRYPTO_RESCAN_HOURS` (12h) it scans **all** Kraken EUR pairs,
filters for real liquidity (≥ `CRYPTO_MIN_EUR_VOLUME`, €2M/day, stablecoins
and darkpool pairs excluded), and scores the most-traded coins on 1-hour
momentum (24h/48h rise, bonus when price sits at the 20-hour high). The
strongest risers become the breakout strategy's universe; coins with an
open position are never swapped out, and only rising coins qualify — in a
falling market the universe simply shrinks. The fixed `CRYPTO_SYMBOLS`
list is the seed until the first scan completes (and the whole universe
when auto mode is off).

### Notes on shorting (EU)

Shorting UCITS ETFs requires an IBKR **margin account** plus borrowable
shares, so shorts are **off by default** (`IBKR_ALLOW_SHORTS=0`): short
signals from the mean-reversion and trend strategies are then treated as
"exit/stay flat". Set `IBKR_ALLOW_SHORTS=1` if you have a margin account
and know what you're doing. BTC is spot-only and is never shorted.

## Setup (US profile)

Set `BOT_MARKET=us`, fill in the Alpaca keys in `.env`
(paper API keys from [app.alpaca.markets](https://app.alpaca.markets)), and
`pip install alpaca-trade-api`. Everything else is identical.

## Running 24/7 on a server (VPS)

On the same Ubuntu/Debian server that runs the original crypto bot (or a
fresh one):

```bash
cd Trade-bot   # the cloned repo
sudo bash deploy/multibot-install.sh
sudo nano /etc/multi-bot.env                              # IBKR login (paper!), Kraken keys
cd /opt/multi-bot/ib-gateway && sudo docker compose up -d # headless IB Gateway
sudo systemctl start multi-bot
sudo journalctl -u multi-bot -f                           # watch the logs
```

This installs the bot as the `multi-bot` systemd service (next to, and
independent of, the existing `trade-bot` service) and runs **IB Gateway
headless in Docker** via [gnzsnz/ib-gateway-docker](https://github.com/gnzsnz/ib-gateway-docker),
which logs in automatically and survives IBKR's forced daily restart. Both
come back automatically after a reboot; the bot restores its positions from
the state files. The gateway's API port is bound to localhost only — never
expose it to the internet.

Note for **live** (not paper) IBKR accounts: two-factor authentication via
the IB Key app means you must confirm the gateway's session roughly weekly;
fully unattended operation is only possible in paper mode.

## How it runs

- every 30 seconds the loop checks hard/trailing stops against the latest price;
- whenever a 15-minute / 1-hour / 4-hour candle closes, the matching strategy
  is evaluated on completed candles only;
- equities trade only during their exchange's hours (Xetra 09:00–17:30 for
  the EU profile, the Alpaca market clock for the US profile); Bitcoin
  trades 24/7;
- broker/API failures are retried with exponential backoff.

Stop with `Ctrl+C` — open positions remain open and are picked up again on
restart via `alpaca_state.json` (entry price, stops and trailing state are
persisted and reconciled against the brokers' real positions at startup).

## Mobile dashboard

The bot serves a phone-friendly dashboard (default port **8081**; the
original crypto bot uses 8080). It shows live equity, open positions with
entry/stop/unrealized P&L, the screened US stock universe and recent
trades, and has a **pause** button (no new entries; stops stay active) and
an **emergency stop** (sell everything; markets that are closed follow at
the open). The buttons require the access code (`WEB_CODE` in `.env`, or a
random code printed in the logs); viewing does not.

By default it binds to `127.0.0.1` only. On a VPS, view it through an SSH
tunnel (`ssh -L 8081:localhost:8081 root@<server>` → open
`http://localhost:8081`) or via Tailscale. `WEB_DASHBOARD=0` disables it;
`WEB_HOST`/`WEB_PORT` change the binding.

## Logs & notifications

- `trades.csv` — one row per completed round-trip: timestamp, instrument,
  direction, entry price, exit price, P&L and position size.
- `daily_pnl.csv` — one row per calendar day (Amsterdam time for EU, New
  York for US): start equity, end equity and P&L.
- **Telegram** (optional): set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`
  in `.env` (same setup as the original crypto bot — via @BotFather) and
  you get a phone notification for every entry/exit with its reason and
  P&L, the daily result, changes to the screened US stock universe, and
  errors (rate-limited to once per 15 minutes).

## Tests

```bash
python -m unittest discover -s tests -v
```

The indicator, strategy, risk-manager and broker-wiring modules are pure
Python and tested without any API access or broker SDK installed.

## ⚠️ Disclaimer

Trading is risky and this bot offers no guarantee of profit whatsoever. Run
it on paper accounts first — for weeks — before even considering live money,
and never trade money you cannot afford to lose. Tax note for EU users:
gains remain taxable (in NL: box 3); US-source dividends are subject to
treaty withholding via the W-8BEN your broker files.
