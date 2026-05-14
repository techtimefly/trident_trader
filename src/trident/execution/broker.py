from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from trident.execution.orders import BracketOrderIntent


@dataclass(frozen=True)
class SubmittedOrder:
    broker_order_id: str
    client_order_id: str
    status: str  # "new" | "accepted" | "pending_new" | ...


@dataclass(frozen=True)
class OrderSnapshot:
    """Subset of a broker order that we care about."""

    broker_order_id: str
    client_order_id: str
    symbol: str
    side: str
    qty: int
    filled_qty: int
    avg_fill_price: Decimal | None
    status: str
    order_class: str
    submitted_at: str
    filled_at: str | None


@dataclass(frozen=True)
class PositionSnapshot:
    symbol: str
    qty: int  # signed: negative = short
    avg_entry_price: Decimal
    market_value: Decimal
    unrealized_pl: Decimal


class Broker(Protocol):
    """The execution surface used by the runner. Only the v0.2-needed methods."""

    def submit_bracket(self, intent: BracketOrderIntent) -> SubmittedOrder: ...

    def cancel_all_orders(self) -> int: ...

    def close_all_positions(self, cancel_orders: bool = True) -> int: ...

    def list_open_orders(self) -> list[OrderSnapshot]: ...

    def list_orders_since(self, iso_ts: str) -> list[OrderSnapshot]: ...

    def get_order_by_client_id(self, client_order_id: str) -> OrderSnapshot | None: ...

    def list_positions(self) -> list[PositionSnapshot]: ...
