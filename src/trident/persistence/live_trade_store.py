"""Persist closed round-trips to the ``live_trades`` table.

Thin DB layer over the pure :mod:`trident.accounting.round_trip` computation —
the caller computes the :class:`RoundTrip`, this writes the row. Middle ring;
DB-backed, verified by smoke rather than unit tests.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from trident.accounting.round_trip import WASH_SALE_WINDOW, RoundTrip
from trident.persistence.models import LiveTrade
from trident.persistence.session import session_scope


def wash_check_entries(symbol: str, around_ts: datetime) -> list[tuple[str, datetime]]:
    """Entry timestamps of already-recorded trades in ``symbol`` within the
    wash-sale window of ``around_ts`` — the ``other_entries`` for is_wash_sale.
    """
    lo = around_ts - WASH_SALE_WINDOW
    hi = around_ts + WASH_SALE_WINDOW
    with session_scope() as s:
        rows = s.scalars(
            select(LiveTrade).where(
                LiveTrade.symbol == symbol,
                LiveTrade.entry_ts >= lo,
                LiveTrade.entry_ts <= hi,
            )
        )
        return [(r.symbol, r.entry_ts) for r in rows]


def record_live_trade(
    rt: RoundTrip,
    *,
    strategy: str,
    wash_sale: bool,
    entry_order_id: uuid.UUID | None = None,
    exit_order_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """Persist one closed round-trip. Returns the new row's id."""
    new_id = uuid.uuid4()
    with session_scope() as s:
        s.add(
            LiveTrade(
                id=new_id,
                symbol=rt.symbol,
                side=rt.side,
                strategy=strategy,
                qty=rt.qty,
                entry_ts=rt.entry_ts,
                entry_price=rt.entry_price,
                exit_ts=rt.exit_ts,
                exit_price=rt.exit_price,
                gross_pnl=rt.gross_pnl,
                fees=rt.fees,
                net_pnl=rt.net_pnl,
                r_multiple=rt.r_multiple,
                holding_period_seconds=rt.holding_period_seconds,
                wash_sale=wash_sale,
                entry_order_id=entry_order_id,
                exit_order_id=exit_order_id,
                created_at=datetime.now(UTC),
            )
        )
    from trident.audit.log import record

    record(
        "live_trade_recorded",
        actor="portfolio.tracking",
        payload={
            "id": str(new_id),
            "symbol": rt.symbol,
            "side": rt.side,
            "net_pnl": str(rt.net_pnl),
            "wash_sale": wash_sale,
        },
    )
    return new_id
