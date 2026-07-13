# Alpaca multi-instrument trading bot

A Python trading bot for the [Alpaca Markets](https://alpaca.markets) API that
trades **5 instruments simultaneously** — SPY, QQQ, BTC/USD, GLD and USO —
with three independent strategy modules and strict ATR-based risk management.

> This bot lives next to the original crypto bot in this repo (`trade_bot/`,
> `main.py`); the two are completely independent.

## Strategies

| Module | Instruments | Candles | Logic |
|---|---|---|---|
| `strategies/mean_reversion.py` | SPY, QQQ | 15 min | Long/short when price is > 1.5σ (SPY) / 1.8σ (QQQ) away from the 20-period SMA; exit when price returns to the mean. |
| `strategies/momentum_breakout.py` | BTC/USD | 1 hour | Long on a break above the 20-period high with volume ≥ 1.5× the 20-period average; a confirmed break below the 20-period low exits the long (Alpaca has no crypto shorts). Trailing stop: 2× ATR. |
| `strategies/trend_following.py` | GLD, USO | 4 hours | Long when the 50 EMA crosses above the 200 EMA; short (or exit) on the cross down. Trailing stop: 3× ATR. |

## Risk management (`risk_manager.py`)

- **ATR position sizing**: 14-period ATR per instrument, sized so a 1 ATR
  adverse move equals exactly **1% of account equity**. Quiet instruments get
  bigger positions, volatile ones smaller — dollar risk stays constant.
- **Hard stop**: every position has a stop 1 ATR from the fill price, i.e.
  1% of equity. No exceptions. Stops are checked every 30 seconds.
- **Trailing stops** (breakout and trend strategies) ratchet with the best
  price seen and only ever tighten.
- **Correlation filter**: if SPY **and** QQQ are both long, no new BTC/USD
  longs are opened — no tripling up on risk-on exposure.
- Position notional is additionally capped at 95% of available buying power.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env    # fill in your Alpaca API keys
```

The default `.env` points at Alpaca **paper trading** — fake money, real
market data. For live trading change `ALPACA_BASE_URL` to
`https://api.alpaca.markets` (entirely at your own risk).

## Run

```bash
python -m bot.main
```

The bot runs a continuous loop:

- every 30 seconds it checks hard/trailing stops against the latest price;
- whenever a 15-minute / 1-hour / 4-hour candle closes, the matching strategy
  is evaluated on completed candles only;
- equities (SPY, QQQ, GLD, USO) trade only during regular market hours (via
  the Alpaca market clock); BTC/USD trades 24/7;
- API disconnections are retried with exponential backoff.

Stop with `Ctrl+C` — open positions remain open and are picked up again on
restart via `alpaca_state.json` (entry price, stops and trailing state are
persisted and reconciled against the real Alpaca positions at startup).

## Logs

- `trades.csv` — one row per completed round-trip: timestamp, instrument,
  direction, entry price, exit price, P&L and position size.
- `daily_pnl.csv` — one row per (New York) calendar day: start equity, end
  equity and P&L.

## Tests

```bash
python -m unittest tests.test_alpaca_bot -v
```

The indicator, strategy and risk-manager modules are pure Python and tested
without any API access.

## ⚠️ Disclaimer

Trading is risky and this bot offers no guarantee of profit whatsoever. Run
it on paper trading first — for weeks — before even considering live money,
and never trade money you cannot afford to lose.
