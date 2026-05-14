"""Polling-based order tracking and local position maintenance.

For v0.2 we poll Alpaca every few seconds instead of consuming the trade-updates
WebSocket. The polling approach is simpler, easier to test, and at our trade rate
(a few orders per day) the latency cost doesn't matter. The trade-updates stream
becomes valuable in v0.3+ once we want second-by-second fill events.

What this module does:
  - For every order Alpaca knows about today, update our local `orders` row.
  - When the parent entry of a bracket fills, insert a row into `positions`.
  - When the position closes (TP/SL or manual flatten), remove the row.

Audit events:
  - order_state_changed — on any state transition (pending → submitted → filled etc).
  - position_opened / position_closed — derived from the order state changes.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select

from trident.audit.log import get_logger, record
from trident.execution.broker import Broker, OrderSnapshot
from trident.persistence.models import Order as OrderRow
from trident.persistence.models import Position as PositionRow
from trident.persistence.session import session_scope

log = get_logger("portfolio.tracking")


def sync_orders(broker: Broker, since_iso: str) -> int:
    """Pull every order touched since `since_iso` and reconcile with the local DB.

    Returns the number of orders updated (created or state-changed).
    """
    snaps = broker.list_orders_since(since_iso)
    changed = 0

    with session_scope() as s:
        for snap in snaps:
            if not snap.client_order_id:
                continue  # Bracket-order children inherit the parent id; we only track our originals.

            row = _get_by_client_id(s, snap.client_order_id)
            if row is None:
                row = OrderRow(
                    id=uuid.uuid4(),
                    client_order_id=snap.client_order_id,
                    broker_order_id=snap.broker_order_id,
                    symbol=snap.symbol,
                    side=snap.side,
                    qty=snap.qty,
                    order_type="bracket",
                    state=snap.status,
                    raw={"snapshot": _snap_to_dict(snap)},
                )
                s.add(row)
                _audit_state_change(snap, old_state=None)
                changed += 1
                continue

            if row.state != snap.status:
                old = row.state
                row.state = snap.status
                row.broker_order_id = snap.broker_order_id or row.broker_order_id
                if snap.avg_fill_price is not None:
                    row.avg_fill_price = snap.avg_fill_price
                if snap.filled_at:
                    row.filled_at = _parse_iso(snap.filled_at)
                _audit_state_change(snap, old_state=old)
                changed += 1

    return changed


def reconcile_positions(broker: Broker) -> dict[str, object]:
    """Compare local positions vs broker. Mutates local DB to match broker.

    Drift is logged and audited but the broker is treated as authoritative
    (the broker is the source of truth for what we actually own).
    """
    broker_pos = {p.symbol: p for p in broker.list_positions()}

    drift: list[dict[str, object]] = []
    with session_scope() as s:
        local = {p.symbol: p for p in s.scalars(select(PositionRow))}

        # Remove or update existing local rows
        for symbol, local_pos in list(local.items()):
            if symbol not in broker_pos:
                drift.append({"symbol": symbol, "kind": "local_only", "local_qty": local_pos.qty})
                s.delete(local_pos)
                record(
                    "position_closed",
                    actor="reconciler",
                    payload={"symbol": symbol, "local_qty": local_pos.qty},
                )
                continue
            bp = broker_pos[symbol]
            if bp.qty != local_pos.qty:
                drift.append(
                    {
                        "symbol": symbol,
                        "kind": "qty_mismatch",
                        "local_qty": local_pos.qty,
                        "broker_qty": bp.qty,
                    }
                )
                local_pos.qty = bp.qty
                local_pos.avg_entry = bp.avg_entry_price

        # Add positions present in broker but missing locally
        for symbol, bp in broker_pos.items():
            if symbol in local:
                continue
            drift.append({"symbol": symbol, "kind": "broker_only", "broker_qty": bp.qty})
            s.add(
                PositionRow(
                    id=uuid.uuid4(),
                    symbol=symbol,
                    qty=bp.qty,
                    avg_entry=bp.avg_entry_price,
                    stop_price=Decimal("0"),  # unknown without bracket child lookup; fill later
                    target_price=Decimal("0"),
                    opened_at=datetime.now(UTC),
                )
            )
            record(
                "position_opened",
                actor="reconciler",
                payload={"symbol": symbol, "qty": bp.qty},
            )

    result: dict[str, object] = {
        "ts": datetime.now(UTC).isoformat(),
        "broker_positions": len(broker_pos),
        "drift_events": drift,
        "drift_count": len(drift),
    }
    record("reconciliation_completed", actor="reconciler", payload=result)
    return result


def _get_by_client_id(session, client_order_id: str) -> OrderRow | None:  # type: ignore[no-untyped-def]
    stmt = select(OrderRow).where(OrderRow.client_order_id == client_order_id)
    return session.scalars(stmt).first()


def _audit_state_change(snap: OrderSnapshot, old_state: str | None) -> None:
    record(
        "order_state_changed",
        actor="portfolio.tracking",
        payload={
            "client_order_id": snap.client_order_id,
            "broker_order_id": snap.broker_order_id,
            "symbol": snap.symbol,
            "old_state": old_state,
            "new_state": snap.status,
            "filled_qty": snap.filled_qty,
            "avg_fill_price": str(snap.avg_fill_price) if snap.avg_fill_price else None,
        },
    )


def _snap_to_dict(snap: OrderSnapshot) -> dict[str, object]:
    return {
        "broker_order_id": snap.broker_order_id,
        "client_order_id": snap.client_order_id,
        "symbol": snap.symbol,
        "side": snap.side,
        "qty": snap.qty,
        "filled_qty": snap.filled_qty,
        "avg_fill_price": str(snap.avg_fill_price) if snap.avg_fill_price else None,
        "status": snap.status,
        "order_class": snap.order_class,
        "submitted_at": snap.submitted_at,
        "filled_at": snap.filled_at,
    }


def _parse_iso(s: str) -> datetime:
    # alpaca-py serializes timestamps with a trailing 'Z' sometimes.
    s = s.replace("Z", "+00:00") if s.endswith("Z") else s
    return datetime.fromisoformat(s)
