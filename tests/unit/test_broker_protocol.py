from __future__ import annotations

from decimal import Decimal

from tests.unit.fakes import FakeBroker
from trident.execution.broker import Broker, PositionSnapshot
from trident.execution.orders import OrderIntent


def _intent(reason: str = "scale_in") -> OrderIntent:
    return OrderIntent(
        client_order_id="trident-sig-1-scale_in-1",
        symbol="AAPL",
        side="buy",
        qty=10,
        order_type="market",
        limit_price=None,
        time_in_force="day",
        reason=reason,
    )


def test_fake_broker_satisfies_the_broker_protocol() -> None:
    # Broker is @runtime_checkable, so a structurally-complete double passes.
    assert isinstance(FakeBroker(), Broker)


def test_submit_order_is_recorded() -> None:
    broker = FakeBroker()
    submitted = broker.submit_order(_intent())
    assert broker.submitted_orders == [_intent()]
    assert submitted.client_order_id == "trident-sig-1-scale_in-1"
    assert submitted.status == "accepted"


def test_cancel_order_is_recorded() -> None:
    broker = FakeBroker()
    broker.cancel_order("broker-xyz")
    assert broker.cancelled_orders == ["broker-xyz"]


def test_replace_order_records_only_supplied_fields() -> None:
    broker = FakeBroker()
    broker.replace_order("broker-xyz", stop_price=Decimal("101.50"))
    assert len(broker.replaced_orders) == 1
    call = broker.replaced_orders[0]
    assert call.broker_order_id == "broker-xyz"
    assert call.stop_price == Decimal("101.50")
    assert call.qty is None
    assert call.limit_price is None


def test_close_position_full_and_partial() -> None:
    broker = FakeBroker()
    broker.close_position("AAPL")  # full close
    broker.close_position("MSFT", qty=5)  # partial
    assert broker.closed_positions[0].symbol == "AAPL"
    assert broker.closed_positions[0].qty is None
    assert broker.closed_positions[1].qty == 5


def test_list_positions_round_trips() -> None:
    pos = PositionSnapshot(
        symbol="AAPL",
        qty=10,
        avg_entry_price=Decimal("100"),
        market_value=Decimal("1010"),
        unrealized_pl=Decimal("10"),
    )
    broker = FakeBroker(positions=[pos])
    assert broker.list_positions() == [pos]
