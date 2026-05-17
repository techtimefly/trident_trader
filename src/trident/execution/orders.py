"""Pure helpers for translating Signals into broker order intents.

Kept separate from the Alpaca adapter so the rules (entry buffer, idempotency key,
TP/SL geometry) are easy to read and exhaustively unit-test without any network.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from trident.strategies.base import Signal

# Pay up to this much above the breakout bar's close to get filled. Small enough that
# we don't badly degrade R:R, large enough to absorb normal one-second-bar drift.
ENTRY_LIMIT_BUFFER_BPS = Decimal("10")  # 0.10%


@dataclass(frozen=True)
class BracketOrderIntent:
    """Everything needed to submit a bracket order. Broker-agnostic."""

    client_order_id: str
    symbol: str
    side: str  # "buy" | "sell"
    qty: int
    limit_price: Decimal
    take_profit: Decimal
    stop_loss: Decimal
    time_in_force: str  # "day"
    signal_id: str

    def to_audit_payload(self) -> dict[str, object]:
        return {
            "client_order_id": self.client_order_id,
            "symbol": self.symbol,
            "side": self.side,
            "qty": self.qty,
            "limit_price": str(self.limit_price),
            "take_profit": str(self.take_profit),
            "stop_loss": str(self.stop_loss),
            "time_in_force": self.time_in_force,
            "signal_id": self.signal_id,
        }


@dataclass(frozen=True)
class OrderIntent:
    """A single-leg (non-bracket) order. Broker-agnostic.

    Used for active position management — scale-in adds and explicit exits —
    where a bracket's TP/SL children would be wrong. ``limit_price`` is required
    for a ``limit`` order and must be None for a ``market`` order. ``reason``
    records why the order exists (scale_in | scale_out | exit | manual) and
    appears in the audit trail.
    """

    client_order_id: str
    symbol: str
    side: str  # "buy" | "sell"
    qty: int
    order_type: str  # "market" | "limit"
    limit_price: Decimal | None
    time_in_force: str  # "day"
    reason: str  # scale_in | scale_out | exit | manual

    def to_audit_payload(self) -> dict[str, object]:
        return {
            "client_order_id": self.client_order_id,
            "symbol": self.symbol,
            "side": self.side,
            "qty": self.qty,
            "order_type": self.order_type,
            "limit_price": str(self.limit_price) if self.limit_price is not None else None,
            "time_in_force": self.time_in_force,
            "reason": self.reason,
        }


def _round_price(price: Decimal) -> Decimal:
    """Round to the nearest cent. Alpaca rejects orders with sub-penny prices."""
    return price.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def client_order_id_for(signal_id: uuid.UUID | str) -> str:
    """Deterministic per-signal idempotency key. Resubmitting the same signal
    cannot result in two orders on Alpaca's side."""
    return f"trident-{signal_id}"


def build_bracket(signal: Signal, qty: int, signal_id: uuid.UUID | str) -> BracketOrderIntent:
    """Translate an approved signal + share count into a bracket order intent.

    For longs the limit_price is entry * (1 + buffer); for shorts entry * (1 - buffer).
    Stop and target come from the signal as-is, rounded to cents.
    """
    if qty <= 0:
        raise ValueError(f"qty must be > 0, got {qty}")
    if signal.side not in {"long", "short"}:
        raise ValueError(f"unknown side {signal.side!r}")

    buffer = ENTRY_LIMIT_BUFFER_BPS / Decimal("10000")
    if signal.side == "long":
        limit = signal.entry_price * (Decimal("1") + buffer)
        broker_side = "buy"
    else:
        limit = signal.entry_price * (Decimal("1") - buffer)
        broker_side = "sell"

    return BracketOrderIntent(
        client_order_id=client_order_id_for(signal_id),
        symbol=signal.symbol,
        side=broker_side,
        qty=qty,
        limit_price=_round_price(limit),
        take_profit=_round_price(signal.target_price),
        stop_loss=_round_price(signal.stop_price),
        time_in_force="day",
        signal_id=str(signal_id),
    )


# Why a management order exists. Part of its idempotency key, so the set is
# closed — an unknown reason is a bug, not a free-form label.
MANAGEMENT_REASONS = frozenset({"scale_in", "scale_out", "exit", "manual"})


def management_client_order_id(signal_id: uuid.UUID | str, reason: str, seq: int) -> str:
    """Deterministic idempotency key for a management order on a position.

    Distinct from the bracket key (``trident-{signal_id}``) by the reason+seq
    suffix, so a scale-in, a scale-out and the original entry never collide.
    ``seq`` disambiguates repeated actions of the same kind on one position.
    """
    return f"trident-{signal_id}-{reason}-{seq}"


def build_management_order(
    *,
    signal_id: uuid.UUID | str,
    symbol: str,
    side: str,
    qty: int,
    reason: str,
    seq: int,
    limit_price: Decimal | None = None,
) -> OrderIntent:
    """Translate a position-management decision into a single-leg OrderIntent.

    A None ``limit_price`` builds a market order; a value builds a limit order
    rounded to the cent. The client_order_id is deterministic in
    (signal_id, reason, seq) so resubmitting the same management action cannot
    double up on Alpaca's side.
    """
    if qty <= 0:
        raise ValueError(f"qty must be > 0, got {qty}")
    if side not in {"buy", "sell"}:
        raise ValueError(f"side must be 'buy' or 'sell', got {side!r}")
    if reason not in MANAGEMENT_REASONS:
        raise ValueError(f"unknown management reason {reason!r}")
    if seq < 1:
        raise ValueError(f"seq must be >= 1, got {seq}")

    order_type = "limit" if limit_price is not None else "market"
    return OrderIntent(
        client_order_id=management_client_order_id(signal_id, reason, seq),
        symbol=symbol,
        side=side,
        qty=qty,
        order_type=order_type,
        limit_price=_round_price(limit_price) if limit_price is not None else None,
        time_in_force="day",
        reason=reason,
    )
