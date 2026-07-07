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
import os
import sys

from trade_bot.backtest import run_backtest
from trade_bot.bot import TradeBot
from trade_bot.config import BotConfig
from trade_bot.data import load_candles_csv
from trade_bot.exchange import BinanceExchange
from trade_bot.kraken import KrakenExchange
from trade_bot.market import get_market
from trade_bot.notify import TelegramNotifier
from trade_bot.webapp import Dashboard, local_ip


def resolve_symbol(args: argparse.Namespace) -> None:
    """Vervang het Binance-standaardpaar door het Kraken-equivalent."""
    if args.exchange == "kraken" and args.symbol == "BTCUSDT":
        args.symbol = "XBTUSD"
        print("Kraken gekozen: standaardpaar aangepast naar XBTUSD "
              "(Kraken noemt Bitcoin XBT).")


def build_config(args: argparse.Namespace) -> BotConfig:
    config = BotConfig(
        exchange=args.exchange,
        symbol=args.symbol,
        interval=args.interval,
        strategy=args.strategy,
        fast_period=args.fast,
        slow_period=args.slow,
        rsi_period=args.rsi_period,
        rsi_oversold=args.oversold,
        rsi_overbought=args.overbought,
        macd_fast=args.macd_fast,
        macd_slow=args.macd_slow,
        macd_signal=args.macd_signal,
        start_cash=args.cash,
        position_size=args.size,
        fee_rate=args.fee,
        stop_loss=args.stop_loss,
        take_profit=args.take_profit,
        poll_seconds=getattr(args, "poll", 60),
        max_order=getattr(args, "max_order", 100.0),
        relearn_hours=getattr(args, "relearn_hours", 6.0),
    )
    config.validate()
    return config


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--exchange", default="binance", choices=["binance", "kraken"],
                        help="exchange voor koersdata en orders (standaard binance)")
    parser.add_argument("--symbol", default="BTCUSDT",
                        help="handelspaar (standaard BTCUSDT; Kraken: XBTUSD)")
    parser.add_argument("--interval", default="1h", help="candle-interval, bv. 15m, 1h, 4h, 1d")
    parser.add_argument("--strategy", default="sma_cross",
                        choices=["sma_cross", "rsi", "macd", "auto"],
                        help="handelsstrategie; 'auto' kiest en leert zelf (alleen bij run)")
    parser.add_argument("--fast", type=int, default=10, help="snelle SMA-periode")
    parser.add_argument("--slow", type=int, default=30, help="trage SMA-periode")
    parser.add_argument("--rsi-period", type=int, default=14)
    parser.add_argument("--oversold", type=float, default=30.0)
    parser.add_argument("--overbought", type=float, default=70.0)
    parser.add_argument("--macd-fast", type=int, default=12)
    parser.add_argument("--macd-slow", type=int, default=26)
    parser.add_argument("--macd-signal", type=int, default=9)
    parser.add_argument("--cash", type=float, default=10_000.0, help="startkapitaal (USDT)")
    parser.add_argument("--size", type=float, default=0.95, help="fractie van cash per aankoop")
    parser.add_argument("--fee", type=float, default=0.001, help="handelskosten per order (0.001 = 0.1%%)")
    parser.add_argument("--stop-loss", type=float, default=0.05, help="stop-loss fractie (0.05 = 5%%)")
    parser.add_argument("--take-profit", type=float, default=0.15, help="take-profit fractie")


def cmd_backtest(args: argparse.Namespace) -> int:
    if args.strategy == "auto":
        print("'auto' is alleen voor run; gebruik 'compare' om strategieën te vergelijken.",
              file=sys.stderr)
        return 1
    resolve_symbol(args)
    config = build_config(args)
    if args.csv:
        candles = load_candles_csv(args.csv)
        print(f"{len(candles)} candles geladen uit {args.csv}")
    else:
        market = get_market(config.exchange)
        candles = market.fetch_candles(config.symbol, config.interval, limit=args.limit)
        print(f"{len(candles)} candles opgehaald voor {config.symbol} "
              f"({config.interval}, {market.name})")

    result = run_backtest(candles, config)
    print(f"\nBacktest {config.symbol} — strategie: {config.strategy}")
    print("-" * 44)
    print(result.summary())
    if args.trades:
        print("\nTrades:")
        for t in result.trades:
            print(f"  {t.timestamp:%Y-%m-%d %H:%M}  {t.side:<4} {t.quantity:.6f} @ {t.price:.2f}  ({t.reason})")
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    """Draai alle strategieën op dezelfde data en zet de resultaten naast elkaar."""
    resolve_symbol(args)
    if args.csv:
        candles = load_candles_csv(args.csv)
        print(f"{len(candles)} candles geladen uit {args.csv}")
    else:
        market = get_market(args.exchange)
        candles = market.fetch_candles(args.symbol, args.interval, limit=args.limit)
        print(f"{len(candles)} candles opgehaald voor {args.symbol} "
              f"({args.interval}, {market.name})")

    rows = []
    for name in ("sma_cross", "rsi", "macd"):
        args.strategy = name
        result = run_backtest(candles, build_config(args))
        win_rate = 100.0 * result.wins / max(result.wins + result.losses, 1)
        rows.append((name, result.total_return_pct, result.num_trades, win_rate,
                     result.max_drawdown_pct))

    buy_hold = (candles[-1].close - candles[0].close) / candles[0].close * 100.0
    print(f"\n{'strategie':<12} {'rendement':>10} {'trades':>7} {'winrate':>8} {'max dd':>8}")
    print("-" * 50)
    for name, ret, trades, win_rate, drawdown in sorted(rows, key=lambda r: -r[1]):
        print(f"{name:<12} {ret:>9.2f}% {trades:>7d} {win_rate:>7.1f}% {drawdown:>7.2f}%")
    print("-" * 50)
    print(f"{'buy & hold':<12} {buy_hold:>9.2f}%")
    return 0


def make_exchange(args: argparse.Namespace):
    """Bouw een exchange-koppeling voor --testnet/--live, met bevestiging voor live."""
    if not (args.live or args.testnet):
        return None

    if args.exchange == "kraken":
        if args.testnet:
            print("Kraken heeft geen testnet voor spot-handel. Oefen met paper trading\n"
                  "(gewoon zonder --testnet/--live starten) en ga daarna pas --live.",
                  file=sys.stderr)
            sys.exit(1)
        key_var, secret_var = "KRAKEN_API_KEY", "KRAKEN_API_SECRET"
    else:
        key_var, secret_var = "BINANCE_API_KEY", "BINANCE_API_SECRET"

    api_key = os.environ.get(key_var, "")
    api_secret = os.environ.get(secret_var, "")
    if not api_key or not api_secret:
        print("FOUT: zet eerst je API-keys als environment variables:", file=sys.stderr)
        print(f"  export {key_var}=...", file=sys.stderr)
        print(f"  export {secret_var}=...", file=sys.stderr)
        if args.testnet:
            print("Testnet-keys maak je gratis aan op https://testnet.binance.vision", file=sys.stderr)
        sys.exit(1)

    if args.live and not args.testnet:
        symbol = args.symbol.upper()
        print("=" * 60)
        print("WAARSCHUWING: LIVE TRADING — er wordt met ECHT GELD gehandeld.")
        print(f"  paar:            {symbol}")
        print(f"  max per order:   {args.max_order:.2f}")
        print(f"  budget (cap):    {args.cash:.2f}")
        print(f"  stop-loss:       {args.stop_loss:.1%}   take-profit: {args.take_profit:.1%}")
        print("=" * 60)
        if sys.stdin.isatty():
            answer = input(f"Typ '{symbol}' om te bevestigen (iets anders = stoppen): ")
            if answer.strip().upper() != symbol:
                print("Geannuleerd — er is niets gebeurd.")
                sys.exit(0)
        elif os.environ.get("LIVE_TRADING_ACK", "").upper() != symbol:
            # geen terminal (bv. systemd): bevestiging moet via env var
            print(f"FOUT: geen terminal voor bevestiging. Zet LIVE_TRADING_ACK={symbol} "
                  "in de environment om live trading zonder terminal te bevestigen.",
                  file=sys.stderr)
            sys.exit(1)

    if args.exchange == "kraken":
        return KrakenExchange(api_key, api_secret)
    return BinanceExchange(api_key, api_secret, testnet=args.testnet)


def cmd_run(args: argparse.Namespace) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    resolve_symbol(args)
    config = build_config(args)
    exchange = make_exchange(args)
    if exchange is None:
        print("Paper trading — er wordt niet met echt geld gehandeld. "
              "Gebruik --testnet of --live voor echte orders.")
    notifier = TelegramNotifier.from_env()
    if notifier:
        print("Telegram-meldingen actief.")
    bot = TradeBot(config, exchange=exchange, notifier=notifier,
                   state_file=args.state or None)
    if args.state:
        print(f"Toestand wordt bewaard in {args.state} (herstart-veilig).")
    if args.web is not None:
        dashboard = Dashboard(bot, port=args.web)
        dashboard.start()
        print(f"\n📱 Dashboard voor op je telefoon (zelfde wifi-netwerk):")
        print(f"   http://{local_ip()}:{dashboard.port}")
        print(f"   Toegangscode voor de knoppen: {dashboard.token}")
        print(f"   Tip: 'Toevoegen aan beginscherm' in je browser maakt er een app van.\n")
    bot.run()
    return 0


def cmd_price(args: argparse.Namespace) -> int:
    resolve_symbol(args)
    market = get_market(args.exchange)
    print(f"{args.symbol.upper()} ({market.name}): {market.fetch_price(args.symbol)}")
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

    p_compare = sub.add_parser("compare", help="alle strategieën vergelijken op dezelfde data")
    add_common_args(p_compare)
    p_compare.add_argument("--csv", help="pad naar CSV in plaats van Binance-data")
    p_compare.add_argument("--limit", type=int, default=500, help="aantal candles op te halen (max 1000)")
    p_compare.set_defaults(func=cmd_compare)

    p_run = sub.add_parser("run", help="trading-loop starten (standaard paper trading)")
    add_common_args(p_run)
    p_run.add_argument("--poll", type=int, default=60, help="seconden tussen polls")
    p_run.add_argument("--testnet", action="store_true",
                       help="echte orders op het Binance-TESTNET (nepgeld, oefenen)")
    p_run.add_argument("--live", action="store_true",
                       help="echte orders met ECHT GELD (vraagt bevestiging)")
    p_run.add_argument("--max-order", type=float, default=100.0,
                       help="max bedrag per live aankoop in quote-valuta (standaard 100)")
    p_run.add_argument("--web", type=int, nargs="?", const=8080, default=None,
                       metavar="POORT",
                       help="start het mobiele dashboard (standaardpoort 8080)")
    p_run.add_argument("--state", default="bot_state.json", metavar="PAD",
                       help="bestand voor de bot-toestand ('' = uit, standaard bot_state.json)")
    p_run.add_argument("--relearn-hours", type=float, default=6.0,
                       help="bij --strategy auto: elke zoveel uur opnieuw evalueren")
    p_run.set_defaults(func=cmd_run)

    p_price = sub.add_parser("price", help="huidige prijs opvragen")
    p_price.add_argument("--exchange", default="binance", choices=["binance", "kraken"])
    p_price.add_argument("--symbol", default="BTCUSDT")
    p_price.set_defaults(func=cmd_price)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
