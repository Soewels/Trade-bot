"""Backtester: draai een strategie over historische candles en rapporteer resultaten."""

from dataclasses import dataclass

from .config import BotConfig
from .data import Candle
from .portfolio import Portfolio, Trade
from .strategy import Signal, build_strategy


@dataclass
class BacktestResult:
    start_cash: float
    final_equity: float
    total_return_pct: float
    buy_hold_return_pct: float
    num_trades: int
    wins: int
    losses: int
    max_drawdown_pct: float
    trades: list[Trade]

    def summary(self) -> str:
        win_rate = 100.0 * self.wins / max(self.wins + self.losses, 1)
        return "\n".join([
            f"Startkapitaal:      {self.start_cash:>12.2f}",
            f"Eindwaarde:         {self.final_equity:>12.2f}",
            f"Rendement:          {self.total_return_pct:>11.2f}%",
            f"Buy & hold:         {self.buy_hold_return_pct:>11.2f}%",
            f"Aantal trades:      {self.num_trades:>12d}",
            f"Winst/verlies:      {self.wins:>5d} / {self.losses} (winrate {win_rate:.1f}%)",
            f"Max drawdown:       {self.max_drawdown_pct:>11.2f}%",
        ])


def run_backtest(candles: list[Candle], config: BotConfig) -> BacktestResult:
    config.validate()
    if len(candles) < 2:
        raise ValueError("te weinig candles voor een backtest")

    strategy = build_strategy(config)
    portfolio = Portfolio(cash=config.start_cash, fee_rate=config.fee_rate)

    closes: list[float] = []
    peak_equity = config.start_cash
    max_drawdown = 0.0
    entry_price = 0.0
    wins = 0
    losses = 0

    for candle in candles:
        closes.append(candle.close)
        price = candle.close

        # Risicobeheer eerst: stop-loss / take-profit binnen de candle-range
        reason = portfolio.check_risk(candle.low, config.stop_loss, config.take_profit) \
            or portfolio.check_risk(candle.high, config.stop_loss, config.take_profit)
        if reason == "stop_loss":
            exit_price = portfolio.entry_price * (1 - config.stop_loss)
            portfolio.sell(exit_price, reason=reason, timestamp=candle.open_time)
            losses += 1
        elif reason == "take_profit":
            exit_price = portfolio.entry_price * (1 + config.take_profit)
            portfolio.sell(exit_price, reason=reason, timestamp=candle.open_time)
            wins += 1
        else:
            signal = strategy.signal(closes)
            if signal is Signal.BUY and not portfolio.in_position:
                trade = portfolio.buy(price, config.position_size, timestamp=candle.open_time)
                if trade:
                    entry_price = trade.price
            elif signal is Signal.SELL and portfolio.in_position:
                trade = portfolio.sell(price, timestamp=candle.open_time)
                if trade:
                    if trade.price > entry_price:
                        wins += 1
                    else:
                        losses += 1

        equity = portfolio.equity(price)
        peak_equity = max(peak_equity, equity)
        drawdown = (peak_equity - equity) / peak_equity
        max_drawdown = max(max_drawdown, drawdown)

    final_price = candles[-1].close
    final_equity = portfolio.equity(final_price)
    first_price = candles[0].close
    buy_hold = (final_price - first_price) / first_price * 100.0

    return BacktestResult(
        start_cash=config.start_cash,
        final_equity=final_equity,
        total_return_pct=(final_equity - config.start_cash) / config.start_cash * 100.0,
        buy_hold_return_pct=buy_hold,
        num_trades=len(portfolio.trades),
        wins=wins,
        losses=losses,
        max_drawdown_pct=max_drawdown * 100.0,
        trades=portfolio.trades,
    )
