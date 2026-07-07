#!/usr/bin/env python3
"""CLI voor de trade bot.

Voorbeelden:
    python main.py backtest --symbol BTCUSDT --interval 1h --strategy sma_cross
    python main.py backtest --csv data/mijn_data.csv --strategy rsi
    python main.py run --symbol ETHUSDT --interval 15m --poll 60
    python main.py price --symbol BTCUSDT
"""

import argparse
import logging
import sys

from trade_bot.backtest import run_backtest
from trade_bot.bot import TradeBot
from trade_bot.config import BotConfig
from trade_bot.data import fetch_candles, fetch_price, load_candles_csv


def build_config(args: argparse.Namespace) -> BotConfig:
    config = BotConfig(
        symbol=args.symbol,
        interval=args.interval,
        strategy=args.strategy,
        fast_period=args.fast,
        slow_period=args.slow,
        rsi_period=args.rsi_period,
        rsi_oversold=args.oversold,
        rsi_overbought=args.overbought,
        start_cash=args.cash,
        position_size=args.size,
        fee_rate=args.fee,
        stop_loss=args.stop_loss,
        take_profit=args.take_profit,
        poll_seconds=getattr(args, "poll", 60),
    )
    config.validate()
    return config


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--symbol", default="BTCUSDT", help="handelspaar (standaard BTCUSDT)")
    parser.add_argument("--interval", default="1h", help="candle-interval, bv. 15m, 1h, 4h, 1d")
    parser.add_argument("--strategy", default="sma_cross", choices=["sma_cross", "rsi"])
    parser.add_argument("--fast", type=int, default=10, help="snelle SMA-periode")
    parser.add_argument("--slow", type=int, default=30, help="trage SMA-periode")
    parser.add_argument("--rsi-period", type=int, default=14)
    parser.add_argument("--oversold", type=float, default=30.0)
    parser.add_argument("--overbought", type=float, default=70.0)
    parser.add_argument("--cash", type=float, default=10_000.0, help="startkapitaal (USDT)")
    parser.add_argument("--size", type=float, default=0.95, help="fractie van cash per aankoop")
    parser.add_argument("--fee", type=float, default=0.001, help="handelskosten per order (0.001 = 0.1%%)")
    parser.add_argument("--stop-loss", type=float, default=0.05, help="stop-loss fractie (0.05 = 5%%)")
    parser.add_argument("--take-profit", type=float, default=0.15, help="take-profit fractie")


def cmd_backtest(args: argparse.Namespace) -> int:
    config = build_config(args)
    if args.csv:
        candles = load_candles_csv(args.csv)
        print(f"{len(candles)} candles geladen uit {args.csv}")
    else:
        candles = fetch_candles(config.symbol, config.interval, limit=args.limit)
        print(f"{len(candles)} candles opgehaald voor {config.symbol} ({config.interval})")

    result = run_backtest(candles, config)
    print(f"\nBacktest {config.symbol} — strategie: {config.strategy}")
    print("-" * 44)
    print(result.summary())
    if args.trades:
        print("\nTrades:")
        for t in result.trades:
            print(f"  {t.timestamp:%Y-%m-%d %H:%M}  {t.side:<4} {t.quantity:.6f} @ {t.price:.2f}  ({t.reason})")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = build_config(args)
    print("LET OP: dit is paper trading — er wordt niet met echt geld gehandeld.")
    TradeBot(config).run()
    return 0


def cmd_price(args: argparse.Namespace) -> int:
    print(f"{args.symbol.upper()}: {fetch_price(args.symbol)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Crypto trade bot (paper trading + backtesting)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_backtest = sub.add_parser("backtest", help="strategie testen op historische data")
    add_common_args(p_backtest)
    p_backtest.add_argument("--csv", help="pad naar CSV in plaats van Binance-data")
    p_backtest.add_argument("--limit", type=int, default=500, help="aantal candles op te halen (max 1000)")
    p_backtest.add_argument("--trades", action="store_true", help="toon individuele trades")
    p_backtest.set_defaults(func=cmd_backtest)

    p_run = sub.add_parser("run", help="live paper-trading loop starten")
    add_common_args(p_run)
    p_run.add_argument("--poll", type=int, default=60, help="seconden tussen polls")
    p_run.set_defaults(func=cmd_run)

    p_price = sub.add_parser("price", help="huidige prijs opvragen")
    p_price.add_argument("--symbol", default="BTCUSDT")
    p_price.set_defaults(func=cmd_price)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
