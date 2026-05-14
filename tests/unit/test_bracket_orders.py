from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trident.execution.orders import build_bracket, client_order_id_for
from trident.strategies.base import Signal


def make_signal(
    side: str = "long",
    entry: str = "100.00",
    stop: str = "99.00",
    target: str = "102.00",
) -> Signal:
    return Signal(
        ts=datetime(2026, 5, 15, 14, 30, tzinfo=UTC),
        strategy="orb_5m",
        symbol="AAPL",
        side=side,
        entry_price=Decimal(entry),
        stop_price=Decimal(stop),
        target_price=Decimal(target),
    )


def test_client_order_id_is_deterministic() -> None:
    sid = uuid.UUID("11111111-1111-1111-1111-111111111111")
    assert client_order_id_for(sid) == "trident-11111111-1111-1111-1111-111111111111"
    assert client_order_id_for(sid) == client_order_id_for(sid)


def test_long_bracket_adds_entry_buffer_above_close() -> None:
    sid = uuid.UUID("22222222-2222-2222-2222-222222222222")
    intent = build_bracket(make_signal(side="long", entry="100.00"), qty=10, signal_id=sid)
    # 10 bps of $100 = $0.10
    assert intent.side == "buy"
    assert intent.limit_price == Decimal("100.10")
    assert intent.qty == 10
    assert intent.time_in_force == "day"
    assert intent.client_order_id == client_order_id_for(sid)


def test_short_bracket_subtracts_entry_buffer() -> None:
    intent = build_bracket(
        make_signal(side="short", entry="100.00", stop="102.00", target="98.00"),
        qty=10,
        signal_id="abc",
    )
    assert intent.side == "sell"
    assert intent.limit_price == Decimal("99.90")
    assert intent.stop_loss == Decimal("102.00")
    assert intent.take_profit == Decimal("98.00")


def test_prices_are_rounded_to_cents() -> None:
    intent = build_bracket(
        make_signal(entry="100.123", stop="99.456", target="101.789"),
        qty=1,
        signal_id="x",
    )
    # 100.123 * 1.001 = 100.223 → 100.22
    assert intent.limit_price == Decimal("100.22")
    assert intent.stop_loss == Decimal("99.46")
    assert intent.take_profit == Decimal("101.79")


def test_zero_qty_rejected() -> None:
    with pytest.raises(ValueError):
        build_bracket(make_signal(), qty=0, signal_id="x")


def test_negative_qty_rejected() -> None:
    with pytest.raises(ValueError):
        build_bracket(make_signal(), qty=-5, signal_id="x")


def test_unknown_side_rejected() -> None:
    sig = Signal(
        ts=datetime.now(UTC),
        strategy="orb_5m",
        symbol="AAPL",
        side="sideways",  # bad
        entry_price=Decimal("100"),
        stop_price=Decimal("99"),
        target_price=Decimal("102"),
    )
    with pytest.raises(ValueError):
        build_bracket(sig, qty=10, signal_id="x")


def test_to_audit_payload_is_json_friendly() -> None:
    import json

    intent = build_bracket(make_signal(), qty=10, signal_id="abc")
    payload = intent.to_audit_payload()
    # Round-trips through json with no custom encoder.
    json.dumps(payload)
