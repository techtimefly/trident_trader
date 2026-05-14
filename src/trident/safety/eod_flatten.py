"""End-of-day flatten: at FLATTEN_OFFSET before the session close, cancel all
open orders and close all positions. The runner schedules this task at startup;
it fires once per session and then unschedules itself.

Why we own this rather than relying on `time_in_force=DAY`:
  - Bracket child orders (TP/SL) stay live until parent fills; we want them gone
    by the close, not at next-day cancellation.
  - Some positions may have been opened by hand outside the bot. We flatten those
    too on the assumption that this is a strict day-trading account.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from trident.audit.log import get_logger, record
from trident.clock import ET, current_session, now_et
from trident.execution.broker import Broker

log = get_logger("safety.eod")

# Fire this many minutes before the session close. MOC submissions on NYSE/Nasdaq
# stop being accepted at ~3:50 PM ET, so we cancel everything and use market orders
# to close at 3:55 PM (or 12:55 PM on early-close days). Five minutes leaves room.
FLATTEN_OFFSET = timedelta(minutes=5)


def seconds_until_flatten() -> float | None:
    """Returns the number of seconds until today's flatten point.

    None if the market isn't trading today, the session is already past the
    flatten point, or the session hasn't started yet (caller can poll).
    """
    sess = current_session()
    if sess is None:
        return None
    flatten_at = sess.close_at - FLATTEN_OFFSET
    now = now_et()
    if now >= flatten_at:
        return None
    return (flatten_at - now).total_seconds()


def is_past_flatten_for(at: datetime) -> bool:
    sess = current_session(at)
    if sess is None:
        return True
    return at.astimezone(ET) >= sess.close_at - FLATTEN_OFFSET


def flatten_now(broker: Broker) -> dict[str, int]:
    """Cancel everything then close everything. Atomic from the bot's view —
    Alpaca's `close_all_positions(cancel_orders=True)` does it in one call."""
    log.info("eod_flatten_starting")
    record("eod_flatten_starting", actor="safety.eod", payload={})
    closed = broker.close_all_positions(cancel_orders=True)
    record(
        "eod_flatten_completed",
        actor="safety.eod",
        payload={"closed_positions": closed},
    )
    log.info("eod_flatten_completed", closed_positions=closed)
    return {"closed_positions": closed}
