"""Startup recovery for signals approved but never submitted.

If the runner crashes between the gate approving a signal and the order being
submitted, that signal is left ``gate_decision == "approved"`` with no row in
``orders``. On restart we do NOT resubmit it — the price has moved, and
reject-on-doubt says a stale entry is not worth chasing. Instead we relabel it
``stale`` and audit it, so the record is honest.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, time

from sqlalchemy import select

from trident.clock import ET
from trident.persistence.models import Order, Signal
from trident.persistence.session import session_scope

_STALE_REASON = "approved but unsubmitted at restart; not resubmitted (price moved)"


def mark_stale_unsubmitted_signals(day: date) -> int:
    """Relabel ``day``'s approved-but-unsubmitted signals as ``stale``.

    A signal approved by the gate with no linked ``orders`` row was approved
    and then lost before submission. This relabels it and audits it; it never
    cancels or resubmits anything, so it is safe even in the rare race where an
    order exists on the broker but ``sync_orders`` has not recorded it yet.
    Returns the count relabelled.
    """
    start = datetime.combine(day, time.min, tzinfo=ET).astimezone(UTC)
    end = datetime.combine(day, time.max, tzinfo=ET).astimezone(UTC)
    with_orders = select(Order.signal_id).where(Order.signal_id.is_not(None))
    ids: list[str] = []
    with session_scope() as s:
        rows = s.scalars(
            select(Signal).where(
                Signal.ts >= start,
                Signal.ts <= end,
                Signal.gate_decision == "approved",
                Signal.id.not_in(with_orders),
            )
        ).all()
        for sig in rows:
            sig.gate_decision = "stale"
            sig.gate_reason = _STALE_REASON
            ids.append(str(sig.id))

    # Audit outside the session so each event is its own write.
    from trident.audit.log import record

    for sid in ids:
        record("signal_marked_stale", actor="signal_recovery", payload={"signal_id": sid})
    return len(ids)
