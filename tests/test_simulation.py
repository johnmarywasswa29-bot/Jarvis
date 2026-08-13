"""Simulation tests."""
from __future__ import annotations

import unittest

from simulation.state import SimulationState, AccountState, Order, OrderStatus, OrderType, Direction
from simulation.engine import SimulationEngine, SimulationError


class TestSimulationEngine(unittest.TestCase):
    def setUp(self):
        self.state = SimulationState()
        self.engine = SimulationEngine(self.state)

    def test_fill_market_order(self):
        self.state.update_market("DEMO", 100.0)
        order = self.engine.submit_order(self._order("DEMO", Direction.BUY, OrderType.MARKET, 1))
        self.assertEqual(order.status, OrderStatus.FILLED)
        self.assertEqual(order.filled_price, 100.0)

    def test_insufficient_funds_rejected(self):
        self.state.update_market("DEMO", 100.0)
        self.state.account.balance = 50.0
        order = self.engine.submit_order(self._order("DEMO", Direction.BUY, OrderType.MARKET, 1))
        self.assertEqual(order.status, OrderStatus.REJECTED)
        self.assertIn("funds", order.error)

    def test_invalid_quantity_rejected(self):
        self.state.update_market("DEMO", 100.0)
        order = self.engine.submit_order(self._order("DEMO", Direction.BUY, OrderType.MARKET, 0))
        self.assertEqual(order.status, OrderStatus.REJECTED)

    def test_limit_order_rejected_without_price(self):
        order = self.engine.submit_order(self._order("DEMO", Direction.BUY, OrderType.LIMIT, 1))
        self.assertEqual(order.status, OrderStatus.REJECTED)

    def test_sell_without_position_rejected(self):
        self.state.update_market("DEMO", 100.0)
        order = self.engine.submit_order(self._order("DEMO", Direction.SELL, OrderType.MARKET, 1))
        self.assertEqual(order.status, OrderStatus.REJECTED)

    def test_cancel_order(self):
        self.state.update_market("DEMO", 100.0)
        order = self.engine.submit_order(self._order("DEMO", Direction.BUY, OrderType.MARKET, 1))
        result = self.engine.cancel_order(order.order_id)
        self.assertFalse(result)

    def test_account_equity_updates(self):
        self.state.update_market("DEMO", 100.0)
        self.engine.submit_order(self._order("DEMO", Direction.BUY, OrderType.MARKET, 1))
        pos = self.state.get_position("DEMO")
        self.state.update_market("DEMO", 110.0)
        self.assertEqual(pos.unrealized_pnl, 10.0)
        self.assertEqual(self.state.account.equity, 100000.0 - 100.0 + 10.0)

    def _order(self, instrument, direction, order_type, quantity):
        return Order(instrument=instrument, direction=direction, order_type=order_type, quantity=quantity)


if __name__ == "__main__":
    unittest.main(verbosity=2)
