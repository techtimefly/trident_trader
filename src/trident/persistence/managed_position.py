"""Read/write the ``managed_positions`` table.

The runner records a managed position when it opens one, re-reads the open set
each bar to drive the management loop, updates the live stop when a trail is
applied, and removes the row when the position is fully closed.

DB-backed accessors, middle ring — not unit-tested (unit tests are no-DB),
verified by the runner smoke path. The management *logic* lives in the pure,
fully-tested :mod:`trident.portfolio.manage`.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select

from trident.persistence.models import ManagedPosition
from trident.persistence.session import session_scope
from trident.strategies.management import ManagedPositionView


def record_open(
    *,
    symbol: str,
    strategy: str,
    side: str,
    qty: int,
    avg_entry: Decimal,
    stop_price: Decimal,
    target_price: Decimal,
    entry_order_id: uuid.UUID | None = None,
) -> None:
    """Record a newly opened managed position. ``symbol`` is unique — an
    existing row for the symbol is replaced (a re-open after a flat)."""
    now = datetime.now(UTC)
    with session_scope() as s:
        existing = s.scalar(select(ManagedPosition).where(ManagedPosition.symbol == symbol))
        if existing is not None:
            s.delete(existing)
            s.flush()
        s.add(
            ManagedPosition(
                id=uuid.uuid4(),
                symbol=symbol,
                strategy=strategy,
                side=side,
                qty=qty,
                avg_entry=avg_entry,
                stop_price=stop_price,
                target_price=target_price,
                entry_order_id=entry_order_id,
                opened_at=now,
                updated_at=now,
            )
        )


def list_open_for_strategy(strategy: str) -> list[ManagedPositionView]:
    """Open managed positions opened by ``strategy``, as plain views."""
    with session_scope() as s:
        rows = list(
            s.scalars(select(ManagedPosition).where(ManagedPosition.strategy == strategy))
        )
        return [
            ManagedPositionView(
                symbol=r.symbol,
                side=r.side,
                qty=r.qty,
                avg_entry=r.avg_entry,
                stop_price=r.stop_price,
                target_price=r.target_price,
            )
            for r in rows
        ]


def update_stop(symbol: str, new_stop: Decimal) -> None:
    """Persist a trailed stop for ``symbol``'s managed position."""
    with session_scope() as s:
        row = s.scalar(select(ManagedPosition).where(ManagedPosition.symbol == symbol))
        if row is not None:
            row.stop_price = new_stop
            row.updated_at = datetime.now(UTC)


def remove(symbol: str) -> None:
    """Drop the managed-position row once the position is fully closed."""
    with session_scope() as s:
        row = s.scalar(select(ManagedPosition).where(ManagedPosition.symbol == symbol))
        if row is not None:
            s.delete(row)
