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
