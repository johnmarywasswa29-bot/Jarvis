"""Paper execution simulator."""
from __future__ import annotations

from typing import Any

from simulation.state import SimulationState, Order, OrderStatus, OrderType, Direction, Position, Transaction


class SimulationError(Exception):
    pass


class SimulationEngine:
    def __init__(self, state: SimulationState | None = None) -> None:
        self.state = state or SimulationState()

    def submit_order(self, order: Order, *, price: float | None = None) -> Order:
        if order.status != OrderStatus.PENDING:
            return order
        if order.quantity <= 0:
            order.status = OrderStatus.REJECTED
            order.error = "quantity must be positive"
            return order
        if order.order_type == OrderType.LIMIT and (price is None or order.limit_price is None):
            order.status = OrderStatus.REJECTED
            order.error = "limit price required"
            return order
        market_price = price if price is not None else self.state.market.get(order.instrument, 0.0)
        if market_price <= 0:
            order.status = OrderStatus.REJECTED
            order.error = "no market price available"
            return order
        fill_price = market_price if order.order_type == OrderType.MARKET else float(order.limit_price)
        if not self.state.account.can_afford(order.quantity, fill_price):
            order.status = OrderStatus.REJECTED
            order.error = "insufficient funds"
            return order
        order.status = OrderStatus.FILLED
        order.filled_price = fill_price
        order.filled_at = self._now()
        self.state.open_orders.append(order)
        self._apply_fill(order, fill_price)
        return order

    def cancel_order(self, order_id: str) -> bool:
        for order in self.state.open_orders:
            if order.order_id == order_id and order.status == OrderStatus.PENDING:
                order.status = OrderStatus.CANCELLED
                return True
        return False

    def _apply_fill(self, order: Order, fill_price: float) -> None:
        pos = self.state.get_position(order.instrument)
        if order.direction == Direction.BUY:
            required = order.quantity * fill_price
            if self.state.account.balance < required:
                order.status = OrderStatus.REJECTED
                order.error = "insufficient funds"
                return
            new_quantity = pos.quantity + order.quantity
            pos.average_entry_price = (pos.average_entry_price * pos.quantity + order.filled_price * order.quantity) / new_quantity if new_quantity > 0 else 0.0
            pos.quantity = new_quantity
            self.state.account.balance -= required
        else:
            if pos.quantity < order.quantity:
                order.status = OrderStatus.REJECTED
                order.error = "insufficient position"
                return
            pnl = (order.filled_price - pos.average_entry_price) * order.quantity
            pos.quantity -= order.quantity
            self.state.account.balance += order.filled_price * order.quantity + pnl
        self.state.account.available_funds = self.state.account.balance - sum(p.quantity * p.current_price for p in self.state.positions.values() if p.quantity > 0)
        tx = Transaction(order_id=order.order_id, proposal_id=order.proposal_id, instrument=order.instrument, direction=order.direction, quantity=order.quantity, price=order.filled_price)
        self.state.transactions.append(tx)

    @staticmethod
    def _now() -> str:
        from datetime import datetime, UTC
        return datetime.now(UTC).replace(tzinfo=None).isoformat()
