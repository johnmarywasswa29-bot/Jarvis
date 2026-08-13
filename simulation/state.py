"""Simulation state models."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, UTC
from enum import Enum
from typing import Any


class OrderStatus(str, Enum):
    PENDING = "pending"
    FILLED = "filled"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"


class Direction(str, Enum):
    BUY = "buy"
    SELL = "sell"


@dataclass
class Position:
    instrument: str
    quantity: float = 0.0
    average_entry_price: float = 0.0
    current_price: float = 0.0

    @property
    def unrealized_pnl(self) -> float:
        return round((self.current_price - self.average_entry_price) * self.quantity, 4)


@dataclass
class Order:
    order_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    proposal_id: str = ""
    instrument: str = ""
    direction: Direction = Direction.BUY
    order_type: OrderType = OrderType.MARKET
    quantity: float = 0.0
    limit_price: float | None = None
    stop_price: float | None = None
    status: OrderStatus = OrderStatus.PENDING
    filled_price: float = 0.0
    filled_at: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None).isoformat())
    error: str = ""


@dataclass
class Transaction:
    transaction_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    order_id: str = ""
    proposal_id: str = ""
    instrument: str = ""
    direction: Direction = Direction.BUY
    quantity: float = 0.0
    price: float = 0.0
    fees: float = 0.0
    realized_pnl: float = 0.0
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None).isoformat())


@dataclass
class AccountState:
    account_id: str = "paper-default"
    balance: float = 100000.0
    equity: float = 100000.0
    available_funds: float = 100000.0

    def can_afford(self, quantity: float, price: float) -> bool:
        return self.available_funds >= quantity * price


@dataclass
class SimulationState:
    account: AccountState = field(default_factory=AccountState)
    positions: dict[str, Position] = field(default_factory=dict)
    open_orders: list[Order] = field(default_factory=list)
    transactions: list[Transaction] = field(default_factory=list)
    market: dict[str, float] = field(default_factory=dict)
    proposals: dict[str, Any] = field(default_factory=dict)

    def get_position(self, instrument: str) -> Position:
        return self.positions.setdefault(instrument, Position(instrument=instrument))

    def update_market(self, instrument: str, price: float) -> None:
        self.market[instrument] = price
        pos = self.get_position(instrument)
        pos.current_price = price
        self.account.equity = self.account.balance + sum(p.unrealized_pnl for p in self.positions.values())
