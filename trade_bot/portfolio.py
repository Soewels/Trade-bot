"""Paper-trading portfolio: simuleert orders zonder echt geld."""

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class Trade:
    timestamp: datetime
    side: str            # "BUY" of "SELL"
    price: float
    quantity: float
    fee: float
    reason: str          # bv. "signal", "stop_loss", "take_profit"


@dataclass
class Portfolio:
    cash: float
    fee_rate: float = 0.001
    position: float = 0.0        # hoeveelheid base-asset (bv. BTC)
    entry_price: float = 0.0     # gemiddelde instapprijs van de open positie
    trades: list[Trade] = field(default_factory=list)

    @property
    def in_position(self) -> bool:
        return self.position > 0

    def equity(self, price: float) -> float:
        """Totale waarde van cash + positie tegen de huidige prijs."""
        return self.cash + self.position * price

    def buy(self, price: float, cash_fraction: float, reason: str = "signal",
            timestamp: datetime | None = None) -> Trade | None:
        """Koop voor een fractie van de beschikbare cash. Geen effect als al in positie."""
        if self.in_position or price <= 0:
            return None
        spend = self.cash * cash_fraction
        if spend <= 0:
            return None
        fee = spend * self.fee_rate
        quantity = (spend - fee) / price
        self.cash -= spend
        self.position += quantity
        self.entry_price = price
        trade = Trade(timestamp or datetime.now(timezone.utc), "BUY", price, quantity, fee, reason)
        self.trades.append(trade)
        return trade

    def sell(self, price: float, reason: str = "signal",
             timestamp: datetime | None = None) -> Trade | None:
        """Verkoop de volledige positie. Geen effect zonder positie."""
        if not self.in_position or price <= 0:
            return None
        proceeds = self.position * price
        fee = proceeds * self.fee_rate
        quantity = self.position
        self.cash += proceeds - fee
        self.position = 0.0
        trade = Trade(timestamp or datetime.now(timezone.utc), "SELL", price, quantity, fee, reason)
        self.trades.append(trade)
        self.entry_price = 0.0
        return trade

    def check_risk(self, price: float, stop_loss: float, take_profit: float) -> str | None:
        """Geef 'stop_loss' of 'take_profit' terug als de drempel is geraakt, anders None."""
        if not self.in_position or self.entry_price <= 0:
            return None
        change = (price - self.entry_price) / self.entry_price
        if stop_loss > 0 and change <= -stop_loss:
            return "stop_loss"
        if take_profit > 0 and change >= take_profit:
            return "take_profit"
        return None
