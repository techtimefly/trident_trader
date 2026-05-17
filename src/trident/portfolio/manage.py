"""Apply a strategy's ManagementActions to an open position.

Inner ring. Turns the management vocabulary (TrailStop / ScaleOut /
ClosePosition / ScaleIn) into broker calls and a new stop price to persist.

Risk-reducing actions are applied here directly:
  - TrailStop   — accepted only if it tightens; a loosening trail is rejected.
  - ScaleOut    — partial close via the broker.
  - ClosePosition — full close via the broker.

ScaleIn is new exposure. It is intentionally NOT applied here: a gated add
would need risk/gate.py changes, and Phase 3 keeps the pure gate untouched. A
ScaleIn is recorded and skipped, so a later phase can wire a gated-add path
without this module having silently done something unsafe.

A ClosePosition short-circuits everything else for that position — once you are
exiting, trailing or scaling is moot.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from trident.audit.log import record
from trident.execution.broker import Broker
from trident.strategies.management import (
    ClosePosition,
    ManagedPositionView,
    ManagementAction,
    ScaleIn,
    ScaleOut,
    TrailStop,
)


def tightens_stop(side: str, current_stop: Decimal, new_stop: Decimal) -> bool:
    """True if ``new_stop`` reduces risk versus ``current_stop``.

    For a long, a higher stop is tighter; for a short, a lower stop is tighter.
    """
    if side == "long":
        return new_stop > current_stop
    return new_stop < current_stop


@dataclass
class ManagementOutcome:
    """What :func:`apply_management_actions` did, for the caller to persist."""

    new_stop: Decimal | None = None  # set when a TrailStop tightened the stop
    closed_qty: int = 0  # shares closed by ScaleOut (cumulative)
    fully_closed: bool = False  # a ClosePosition (or a full-size ScaleOut) ran
    applied: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def apply_management_actions(
    broker: Broker,
    position: ManagedPositionView,
    actions: list[ManagementAction],
) -> ManagementOutcome:
    """Execute ``actions`` against ``broker`` for ``position``.

    Returns a :class:`ManagementOutcome` the caller persists (update the live
    stop, or remove the managed_positions row when fully closed). Pure of DB
    access — the broker and the audit log are the only side effects.
    """
    outcome = ManagementOutcome()
    held = abs(position.qty)

    # A full close wins outright — nothing else matters once we are exiting.
    for action in actions:
        if isinstance(action, ClosePosition):
            broker.close_position(position.symbol)
            outcome.fully_closed = True
            outcome.closed_qty = held
            outcome.applied.append(f"close:{action.reason}")
            record(
                "position_management_close",
                actor="portfolio.manage",
                payload={"symbol": position.symbol, "reason": action.reason, "qty": held},
            )
            return outcome

    remaining = held
    for action in actions:
        if isinstance(action, TrailStop):
            if tightens_stop(position.side, position.stop_price, action.new_stop):
                outcome.new_stop = action.new_stop
                outcome.applied.append(f"trail:{action.new_stop}")
                record(
                    "position_stop_trailed",
                    actor="portfolio.manage",
                    payload={
                        "symbol": position.symbol,
                        "old_stop": str(position.stop_price),
                        "new_stop": str(action.new_stop),
                    },
                )
            else:
                outcome.skipped.append(f"trail_loosens:{action.new_stop}")
                record(
                    "position_trail_rejected",
                    actor="portfolio.manage",
                    payload={
                        "symbol": position.symbol,
                        "current_stop": str(position.stop_price),
                        "rejected_stop": str(action.new_stop),
                    },
                )
        elif isinstance(action, ScaleOut):
            qty = min(action.qty, remaining)
            if qty <= 0:
                outcome.skipped.append("scale_out:none_remaining")
                continue
            broker.close_position(position.symbol, qty=qty)
            outcome.closed_qty += qty
            remaining -= qty
            outcome.applied.append(f"scale_out:{qty}")
            record(
                "position_scaled_out",
                actor="portfolio.manage",
                payload={"symbol": position.symbol, "qty": qty, "remaining": remaining},
            )
            if remaining <= 0:
                outcome.fully_closed = True
        elif isinstance(action, ScaleIn):
            # New exposure — not applied here (keeps the pure gate untouched).
            outcome.skipped.append(f"scale_in:{action.qty}")
            record(
                "position_scale_in_skipped",
                actor="portfolio.manage",
                payload={
                    "symbol": position.symbol,
                    "qty": action.qty,
                    "note": "scale-in needs a gated-add path; not wired in Phase 3",
                },
            )

    return outcome
