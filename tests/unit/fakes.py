"""In-memory test doubles. No network, no DB.

``FakeBroker`` implements the full :class:`~trident.execution.broker.Broker`
protocol and records every call, so runner and management logic can be tested
without touching Alpaca.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from trident.execution.broker import OrderSnapshot, PositionSnapshot, SubmittedOrder
from trident.execution.orders import BracketOrderIntent, OrderIntent


@dataclass
class ReplaceCall:
    broker_order_id: str
    qty: int | None
    limit_price: Decimal | None
    stop_price: Decimal | None


@dataclass
class CloseCall:
    symbol: str
    qty: int | None


class FakeBroker:
    """A Broker that records calls and serves a configurable position list."""

    def __init__(self, positions: list[PositionSnapshot] | None = None) -> None:
        self.positions: list[PositionSnapshot] = positions or []
        self.open_orders: list[OrderSnapshot] = []
        self.submitted_brackets: list[BracketOrderIntent] = []
        self.submitted_orders: list[OrderIntent] = []
        self.cancelled_orders: list[str] = []
        self.replaced_orders: list[ReplaceCall] = []
        self.closed_positions: list[CloseCall] = []
        self.cancel_all_calls: int = 0
        self.close_all_calls: list[bool] = []
        self._seq: int = 0

    def _next_id(self) -> str:
        self._seq += 1
        return f"fake-order-{self._seq}"

    def submit_bracket(self, intent: BracketOrderIntent) -> SubmittedOrder:
        self.submitted_brackets.append(intent)
        return SubmittedOrder(self._next_id(), intent.client_order_id, "accepted")

    def cancel_all_orders(self) -> int:
        self.cancel_all_calls += 1
        n = len(self.open_orders)
        self.open_orders = []
        return n

    def close_all_positions(self, cancel_orders: bool = True) -> int:
        self.close_all_calls.append(cancel_orders)
        n = len(self.positions)
        self.positions = []
        return n

    def list_open_orders(self) -> list[OrderSnapshot]:
        return list(self.open_orders)

    def list_orders_since(self, iso_ts: str) -> list[OrderSnapshot]:
        return list(self.open_orders)

    def get_order_by_client_id(self, client_order_id: str) -> OrderSnapshot | None:
        for o in self.open_orders:
            if o.client_order_id == client_order_id:
                return o
        return None

    def list_positions(self) -> list[PositionSnapshot]:
        return list(self.positions)

    def submit_order(self, intent: OrderIntent) -> SubmittedOrder:
        self.submitted_orders.append(intent)
        return SubmittedOrder(self._next_id(), intent.client_order_id, "accepted")

    def cancel_order(self, broker_order_id: str) -> None:
        self.cancelled_orders.append(broker_order_id)

    def replace_order(
        self,
        broker_order_id: str,
        *,
        qty: int | None = None,
        limit_price: Decimal | None = None,
        stop_price: Decimal | None = None,
    ) -> SubmittedOrder:
        self.replaced_orders.append(
            ReplaceCall(broker_order_id, qty, limit_price, stop_price)
        )
        return SubmittedOrder(self._next_id(), "", "replaced")

    def close_position(self, symbol: str, qty: int | None = None) -> SubmittedOrder:
        self.closed_positions.append(CloseCall(symbol, qty))
        return SubmittedOrder(self._next_id(), "", "accepted")
