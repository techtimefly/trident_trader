"""Active position management — the vocabulary a strategy uses to manage an
open position bar by bar.

A strategy proposes *entries* through ``Strategy.on_bar``. A strategy that also
wants to *manage* what it opened — trail the stop, scale in or out, exit early —
implements the optional :class:`ManagesPositions` protocol's ``manage`` method.
It is deliberately a separate protocol, not part of ``Strategy``: entry-only
strategies (ORB, VwapReversion) remain valid ``Strategy`` implementations
untouched, and the runner simply skips management for anything that is not a
``ManagesPositions``.

The four actions map onto the extended Broker surface:
  - ``TrailStop``  -> ``replace_order`` on the protective stop leg
  - ``ScaleIn``    -> a gated entry add via ``submit_order``
  - ``ScaleOut``   -> a partial ``close_position``
  - ``ClosePosition`` -> a full ``close_position``

``ScaleIn`` is new exposure and MUST pass the risk gate. ``TrailStop``,
``ScaleOut`` and ``ClosePosition`` only ever reduce risk, so they bypass the
gate by design.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, runtime_checkable

from trident.data.bars import Bar, BarStore


@dataclass(frozen=True)
class ManagedPositionView:
    """The open position as the managing strategy sees it — a plain value
    object, decoupled from the ``managed_positions`` ORM row."""

    symbol: str
    side: str  # long | short
    qty: int  # signed: negative = short
    avg_entry: Decimal
    stop_price: Decimal
    target_price: Decimal


@dataclass(frozen=True)
class TrailStop:
    """Move the protective stop to ``new_stop``. The runner applies it only if
    it tightens risk (raises a long stop / lowers a short stop); a loosening
    trail is rejected."""

    new_stop: Decimal


@dataclass(frozen=True)
class ScaleIn:
    """Add ``qty`` shares to the position. New exposure — routed through the
    risk gate like any entry."""

    qty: int


@dataclass(frozen=True)
class ScaleOut:
    """Reduce the position by ``qty`` shares — a partial close. Risk-reducing,
    so it bypasses the gate."""

    qty: int


@dataclass(frozen=True)
class ClosePosition:
    """Exit the whole position now. Risk-reducing, so it bypasses the gate."""

    reason: str = "strategy_exit"


ManagementAction = TrailStop | ScaleIn | ScaleOut | ClosePosition


@runtime_checkable
class ManagesPositions(Protocol):
    """Optional strategy capability: manage an open position each bar.

    A strategy implementing ``manage`` is consulted by the runner on every bar
    for each of its open positions. Returning an empty list means 'hold'.
    """

    def manage(
        self, bar: Bar, store: BarStore, position: ManagedPositionView
    ) -> list[ManagementAction]: ...
