# Multi-instrument trading bot (EU & US)

A Python trading bot that trades **5 instruments simultaneously** with three
independent strategy modules and strict ATR-based risk management. It ships
with two market profiles, selected via `BOT_MARKET` in `.env`:

| | `eu` (default) | `us` |
|---|---|---|
| S&P 500 | **SXR8** — iShares Core S&P 500 UCITS (Xetra, EUR) | SPY |
| Nasdaq 100 | **SXRV** — iShares Nasdaq 100 UCITS (Xetra, EUR) | QQQ |
| Bitcoin | **BTC/EUR** via Kraken | BTC/USD via Alpaca |
| Gold | **4GLD** — Xetra-Gold ETC (EUR) | GLD |
| Oil | **OD7F** — WisdomTree WTI Crude Oil (Xetra, EUR) | USO |
| Broker | Interactive Brokers + Kraken | Alpaca |
| Currency | EUR | USD |
| P&L calendar | Europe/Amsterdam | America/New_York |

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

### Self-screened US stocks (long & short)

Individual US **stocks** are PRIIPs-exempt (unlike US ETFs), so EU retail
can trade them freely — and they're the easiest instruments to short. The
bot finds them itself:

1. at startup (and weekly, `US_STOCK_RESCAN_DAYS`) it asks the **IBKR
   market scanner** for the most active US stocks above `US_STOCK_MIN_PRICE`
   and a $10B market cap;
2. it ranks the candidates on **average daily dollar volume** computed from
   real daily bars and keeps those above `US_STOCK_MIN_DOLLAR_VOLUME`
   ($50M/day by default);
3. the top `US_STOCK_COUNT` (default 3, `0` = off) join the mean-reversion
   strategy with a wider 2.0σ entry band (single stocks are noisier than
   index trackers), long and — with `IBKR_ALLOW_SHORTS=1` and a margin
   account — short.

On a rescan, stocks with an **open position are never swapped out**; only
flat slots are refreshed. The chosen universe is persisted in the state
file, so a restart keeps managing existing positions. Position sizing
converts the USD ATR to EUR at the live exchange rate, so the 1%-of-equity
risk rule holds exactly in your account currency. US stocks trade during
US market hours (15:30–22:00 CET).

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

## Logs

- `trades.csv` — one row per completed round-trip: timestamp, instrument,
  direction, entry price, exit price, P&L and position size.
- `daily_pnl.csv` — one row per calendar day (Amsterdam time for EU, New
  York for US): start equity, end equity and P&L.

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
