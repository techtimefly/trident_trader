"""Polling-based order tracking and local position maintenance.

For v0.2 we poll Alpaca every few seconds instead of consuming the trade-updates
WebSocket. The polling approach is simpler, easier to test, and at our trade rate
(a few orders per day) the latency cost doesn't matter. The trade-updates stream
becomes valuable in v0.3+ once we want second-by-second fill events.

What this module does:
  - For every order Alpaca knows about today, update our local `orders` row.
  - Record a bracket's child legs (TP/SL) linked to their parent entry order.
  - Reconcile the local `positions` table against the broker.
  - Reconcile the actively-managed `managed_positions` table — drop rows whose
    position the broker no longer holds, and seed a broker-only position's
    stop/target from the managed row's live values.

Audit events:
  - order_state_changed — on any state transition (pending → submitted → filled).
  - position_opened / position_closed — derived from the order state changes.
  - managed_position_closed — a managed position the broker no longer holds.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from trident.accounting.round_trip import (
    ExitCandidate,
    compute_round_trip,
    is_wash_sale,
    pick_exit_order,
)
from trident.audit.log import get_logger, record
from trident.execution.broker import Broker, OrderSnapshot
from trident.persistence.live_trade_store import record_live_trade, wash_check_entries
from trident.persistence.models import ManagedPosition as ManagedPositionRow
from trident.persistence.models import Order as OrderRow
from trident.persistence.models import Position as PositionRow
from trident.persistence.session import session_scope

log = get_logger("portfolio.tracking")


def child_leg_client_id(parent_client_order_id: str, leg_broker_id: str) -> str:
    """Synthetic, unique client_order_id for a bracket child leg.

    Child legs carry no client_order_id of ours, but the `orders` table's
    client_order_id is unique and not-null — so a leg row gets a deterministic
    synthetic id derived from its parent and its own broker id.
    """
    return f"{parent_client_order_id}::leg::{leg_broker_id}"


def managed_symbols_to_drop(managed: Iterable[str], broker: Iterable[str]) -> list[str]:
    """Managed-position symbols the broker no longer holds — i.e. closed."""
    held = set(broker)
    return sorted(s for s in set(managed) if s not in held)


def sync_orders(broker: Broker, since_iso: str) -> int:
    """Pull every order touched since `since_iso` and reconcile with the local DB.

    Parent orders are tracked by our client_order_id; each parent's child legs
    (TP/SL) are recorded too, linked back via `parent_order_id`. Returns the
    number of orders created or state-changed.
    """
    snaps = broker.list_orders_since(since_iso)
    by_broker_id = {sn.broker_order_id: sn for sn in snaps if sn.broker_order_id}
    changed = 0

    with session_scope() as s:
        for snap in snaps:
            if not snap.client_order_id:
                continue  # A child leg — recorded below from its parent's `legs`.

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
            elif row.state != snap.status:
                old = row.state
                row.state = snap.status
                row.broker_order_id = snap.broker_order_id or row.broker_order_id
                if snap.avg_fill_price is not None:
                    row.avg_fill_price = snap.avg_fill_price
                if snap.filled_at:
                    row.filled_at = _parse_iso(snap.filled_at)
                _audit_state_change(snap, old_state=old)
                changed += 1

            changed += _record_legs(s, row, snap, by_broker_id)

    return changed


def _record_legs(
    s: Session,
    parent_row: OrderRow,
    parent_snap: OrderSnapshot,
    by_broker_id: dict[str, OrderSnapshot],
) -> int:
    """Record any of `parent_snap`'s child legs not yet in the DB. Returns the
    count newly recorded. A leg is recorded only when its own snapshot is in the
    batch, so symbol/side/qty/state are accurate."""
    recorded = 0
    for leg_broker_id in parent_snap.legs:
        leg_snap = by_broker_id.get(leg_broker_id)
        if leg_snap is None:
            continue
        leg_cid = child_leg_client_id(parent_snap.client_order_id, leg_broker_id)
        if _get_by_client_id(s, leg_cid) is not None:
            continue
        s.add(
            OrderRow(
                id=uuid.uuid4(),
                client_order_id=leg_cid,
                broker_order_id=leg_broker_id,
                parent_order_id=parent_row.id,
                symbol=leg_snap.symbol,
                side=leg_snap.side,
                qty=leg_snap.qty,
                order_type="bracket_leg",
                state=leg_snap.status,
                raw={"snapshot": _snap_to_dict(leg_snap)},
            )
        )
        recorded += 1
    return recorded


def reconcile_positions(broker: Broker) -> dict[str, object]:
    """Compare local positions vs broker. Mutates local DB to match broker.

    Drift is logged and audited but the broker is treated as authoritative.
    Also reconciles `managed_positions`: a managed position the broker no longer
    holds is dropped, and a broker-only position's stop/target is seeded from
    the managed row's live values rather than left at zero.
    """
    broker_pos = {p.symbol: p for p in broker.list_positions()}

    drift: list[dict[str, object]] = []
    closes: list[_CloseFacts] = []
    with session_scope() as s:
        managed = {m.symbol: m for m in s.scalars(select(ManagedPositionRow))}
        local = {p.symbol: p for p in s.scalars(select(PositionRow))}

        # Remove or update existing local rows.
        for symbol, local_pos in list(local.items()):
            if symbol not in broker_pos:
                drift.append({"symbol": symbol, "kind": "local_only", "local_qty": local_pos.qty})
                # Capture the close facts before deleting — recorded as a
                # LiveTrade after this session closes.
                mp = managed.get(symbol)
                closes.append(
                    _CloseFacts(
                        symbol=symbol,
                        qty=local_pos.qty,
                        avg_entry=local_pos.avg_entry,
                        stop_price=local_pos.stop_price,
                        opened_at=local_pos.opened_at,
                        strategy=mp.strategy if mp is not None else "unknown",
                    )
                )
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

        # Add positions present in broker but missing locally. Seed stop/target
        # from the managed row's live values when we have one.
        for symbol, bp in broker_pos.items():
            if symbol in local:
                continue
            mp = managed.get(symbol)
            drift.append({"symbol": symbol, "kind": "broker_only", "broker_qty": bp.qty})
            s.add(
                PositionRow(
                    id=uuid.uuid4(),
                    symbol=symbol,
                    qty=bp.qty,
                    avg_entry=bp.avg_entry_price,
                    stop_price=mp.stop_price if mp is not None else Decimal("0"),
                    target_price=mp.target_price if mp is not None else Decimal("0"),
                    opened_at=datetime.now(UTC),
                )
            )
            record(
                "position_opened",
                actor="reconciler",
                payload={"symbol": symbol, "qty": bp.qty},
            )

        # Reconcile managed_positions: drop any the broker no longer holds.
        for symbol in managed_symbols_to_drop(managed.keys(), broker_pos.keys()):
            s.delete(managed[symbol])
            drift.append({"symbol": symbol, "kind": "managed_closed"})
            record(
                "managed_position_closed",
                actor="reconciler",
                payload={"symbol": symbol},
            )

    # Record each closed position as a LiveTrade — done after the session above
    # so each LiveTrade write is its own transaction.
    for cf in closes:
        _record_live_trade_for_close(cf)

    result: dict[str, object] = {
        "ts": datetime.now(UTC).isoformat(),
        "broker_positions": len(broker_pos),
        "drift_events": drift,
        "drift_count": len(drift),
    }
    record("reconciliation_completed", actor="reconciler", payload=result)
    return result


@dataclass(frozen=True)
class _CloseFacts:
    """The facts of a position close, captured for LiveTrade recording."""

    symbol: str
    qty: int  # signed: negative = short
    avg_entry: Decimal
    stop_price: Decimal
    opened_at: datetime
    strategy: str


def _record_live_trade_for_close(cf: _CloseFacts) -> None:
    """Match a closed position to its exit order and record a LiveTrade.

    The exit order is the most recent filled order of the opposite broker side
    since the position opened. When none is found the close is audited but no
    LiveTrade is written — P&L is never fabricated. Live per-order fees are not
    tracked (the orders table has no fee column), so ``fees`` is zero here.
    """
    side = "long" if cf.qty >= 0 else "short"
    qty = abs(cf.qty)
    if qty == 0:
        return

    with session_scope() as s:
        rows = s.scalars(
            select(OrderRow).where(
                OrderRow.symbol == cf.symbol,
                OrderRow.state == "filled",
            )
        ).all()
        candidates = [
            ExitCandidate(side=r.side, filled_at=r.filled_at, avg_fill_price=r.avg_fill_price)
            for r in rows
            if r.filled_at is not None and r.avg_fill_price is not None
        ]

    exit_order = pick_exit_order(candidates, side, cf.opened_at)
    if exit_order is None:
        record("live_trade_unmatched", actor="reconciler", payload={"symbol": cf.symbol})
        return

    stop = cf.stop_price if cf.stop_price > 0 else None
    rt = compute_round_trip(
        symbol=cf.symbol,
        side=side,
        qty=qty,
        entry_ts=cf.opened_at,
        entry_price=cf.avg_entry,
        exit_ts=exit_order.filled_at,
        exit_price=exit_order.avg_fill_price,
        fees=Decimal("0"),
        stop_price=stop,
    )
    others = wash_check_entries(cf.symbol, rt.exit_ts)
    wash = is_wash_sale(
        symbol=cf.symbol, exit_ts=rt.exit_ts, net_pnl=rt.net_pnl, other_entries=others
    )
    record_live_trade(rt, strategy=cf.strategy, wash_sale=wash)


def _get_by_client_id(session: Session, client_order_id: str) -> OrderRow | None:
    stmt = select(OrderRow).where(OrderRow.client_order_id == client_order_id)
    row: OrderRow | None = session.scalars(stmt).first()
    return row


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
        "legs": list(snap.legs),
    }


def _parse_iso(s: str) -> datetime:
    # alpaca-py serializes timestamps with a trailing 'Z' sometimes.
    s = s.replace("Z", "+00:00") if s.endswith("Z") else s
    return datetime.fromisoformat(s)
