from __future__ import annotations

from decimal import Decimal

from tests.unit.fakes import FakeBroker
from trident.portfolio.manage import apply_management_actions, tightens_stop
from trident.strategies.management import (
    ClosePosition,
    ManagedPositionView,
    ScaleIn,
    ScaleOut,
    TrailStop,
)


def _long(qty: int = 10, stop: str = "98") -> ManagedPositionView:
    return ManagedPositionView(
        symbol="AAPL",
        side="long",
        qty=qty,
        avg_entry=Decimal("100"),
        stop_price=Decimal(stop),
        target_price=Decimal("106"),
    )


def _short(qty: int = -10, stop: str = "102") -> ManagedPositionView:
    return ManagedPositionView(
        symbol="AAPL",
        side="short",
        qty=qty,
        avg_entry=Decimal("100"),
        stop_price=Decimal(stop),
        target_price=Decimal("94"),
    )


def test_tightens_stop_long_and_short() -> None:
    assert tightens_stop("long", Decimal("98"), Decimal("99")) is True
    assert tightens_stop("long", Decimal("98"), Decimal("97")) is False
    assert tightens_stop("short", Decimal("102"), Decimal("101")) is True
    assert tightens_stop("short", Decimal("102"), Decimal("103")) is False


def test_trail_stop_accepted_when_it_tightens() -> None:
    broker = FakeBroker()
    out = apply_management_actions(broker, _long(), [TrailStop(new_stop=Decimal("99"))])
    assert out.new_stop == Decimal("99")
    assert out.applied == ["trail:99"]


def test_trail_stop_rejected_when_it_loosens() -> None:
    broker = FakeBroker()
    out = apply_management_actions(broker, _long(stop="98"), [TrailStop(new_stop=Decimal("97"))])
    assert out.new_stop is None
    assert out.skipped == ["trail_loosens:97"]


def test_trail_stop_for_short_tightens_downward() -> None:
    broker = FakeBroker()
    out = apply_management_actions(broker, _short(), [TrailStop(new_stop=Decimal("101"))])
    assert out.new_stop == Decimal("101")


def test_scale_out_partial_close() -> None:
    broker = FakeBroker()
    out = apply_management_actions(broker, _long(qty=10), [ScaleOut(qty=4)])
    assert out.closed_qty == 4
    assert out.fully_closed is False
    assert broker.closed_positions[0].symbol == "AAPL"
    assert broker.closed_positions[0].qty == 4


def test_scale_out_is_clamped_to_held_qty_and_marks_fully_closed() -> None:
    broker = FakeBroker()
    out = apply_management_actions(broker, _long(qty=10), [ScaleOut(qty=25)])
    assert out.closed_qty == 10  # clamped to held
    assert out.fully_closed is True


def test_close_position_short_circuits_other_actions() -> None:
    broker = FakeBroker()
    out = apply_management_actions(
        broker,
        _long(qty=10),
        [TrailStop(new_stop=Decimal("99")), ClosePosition(reason="target_near")],
    )
    assert out.fully_closed is True
    assert out.closed_qty == 10
    # The TrailStop after a ClosePosition is moot — not applied.
    assert out.new_stop is None
    assert broker.closed_positions[0].qty is None  # full close


def test_scale_in_is_recorded_but_not_executed() -> None:
    broker = FakeBroker()
    out = apply_management_actions(broker, _long(), [ScaleIn(qty=5)])
    assert out.skipped == ["scale_in:5"]
    # No broker order — a scale-in is not applied without a gated-add path.
    assert broker.submitted_orders == []
    assert broker.closed_positions == []


def test_empty_actions_is_a_hold() -> None:
    broker = FakeBroker()
    out = apply_management_actions(broker, _long(), [])
    assert out.new_stop is None
    assert out.closed_qty == 0
    assert out.fully_closed is False
