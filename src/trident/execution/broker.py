from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, runtime_checkable

from trident.execution.orders import BracketOrderIntent, OrderIntent


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
    # Broker order ids of this order's child legs (a bracket parent's TP/SL).
    # Empty for a child leg or a standalone order.
    legs: tuple[str, ...] = ()


@dataclass(frozen=True)
class PositionSnapshot:
    symbol: str
    qty: int  # signed: negative = short
    avg_entry_price: Decimal
    market_value: Decimal
    unrealized_pl: Decimal


@runtime_checkable
class Broker(Protocol):
    """The execution surface used by the runner.

    The bracket + bulk-flatten methods are the entry/EOD path. The single-order
    and single-position methods (``submit_order``, ``cancel_order``,
    ``replace_order``, ``close_position``) are the active-management path: they
    are what trailing stops, scale-ins and scale-outs, and manual per-position
    control are built on — none of which is expressible with only bulk cancel
    and bulk close.
    """

    def submit_bracket(self, intent: BracketOrderIntent) -> SubmittedOrder: ...

    def cancel_all_orders(self) -> int: ...

    def close_all_positions(self, cancel_orders: bool = True) -> int: ...

    def list_open_orders(self) -> list[OrderSnapshot]: ...

    def list_orders_since(self, iso_ts: str) -> list[OrderSnapshot]: ...

    def get_order_by_client_id(self, client_order_id: str) -> OrderSnapshot | None: ...

    def list_positions(self) -> list[PositionSnapshot]: ...

    # --- Active position management ---------------------------------------

    def submit_order(self, intent: OrderIntent) -> SubmittedOrder:
        """Submit a single-leg (non-bracket) order — a scale-in add or an exit."""
        ...

    def cancel_order(self, broker_order_id: str) -> None:
        """Cancel one order by its broker id (e.g. a stale protective leg)."""
        ...

    def replace_order(
        self,
        broker_order_id: str,
        *,
        qty: int | None = None,
        limit_price: Decimal | None = None,
        stop_price: Decimal | None = None,
    ) -> SubmittedOrder:
        """Modify a live order in place — how a trailing stop moves its stop leg.

        Only the supplied fields change. Alpaca's replace returns a new order id,
        which is reflected in the returned :class:`SubmittedOrder`.
        """
        ...

    def close_position(self, symbol: str, qty: int | None = None) -> SubmittedOrder:
        """Close one position. ``qty`` None closes it entirely; a value does a
        partial close — the primitive a scale-out is built on."""
        ...
