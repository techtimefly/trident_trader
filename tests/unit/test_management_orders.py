from __future__ import annotations

from decimal import Decimal

import pytest

from trident.execution.orders import (
    build_management_order,
    management_client_order_id,
)


def test_market_order_has_no_limit_price() -> None:
    intent = build_management_order(
        signal_id="sig-1", symbol="AAPL", side="sell", qty=5, reason="scale_out", seq=1
    )
    assert intent.order_type == "market"
    assert intent.limit_price is None
    assert intent.symbol == "AAPL"
    assert intent.side == "sell"
    assert intent.qty == 5
    assert intent.reason == "scale_out"


def test_limit_order_rounds_price_to_the_cent() -> None:
    intent = build_management_order(
        signal_id="sig-1",
        symbol="AAPL",
        side="buy",
        qty=10,
        reason="scale_in",
        seq=2,
        limit_price=Decimal("101.23456"),
    )
    assert intent.order_type == "limit"
    assert intent.limit_price == Decimal("101.23")


def test_client_order_id_is_deterministic_in_signal_reason_seq() -> None:
    intent = build_management_order(
        signal_id="sig-9", symbol="MSFT", side="sell", qty=1, reason="exit", seq=3
    )
    assert intent.client_order_id == "trident-sig-9-exit-3"
    assert intent.client_order_id == management_client_order_id("sig-9", "exit", 3)


def test_different_reasons_get_distinct_ids() -> None:
    add = build_management_order(
        signal_id="s", symbol="AAPL", side="buy", qty=1, reason="scale_in", seq=1
    )
    trim = build_management_order(
        signal_id="s", symbol="AAPL", side="sell", qty=1, reason="scale_out", seq=1
    )
    assert add.client_order_id != trim.client_order_id


def test_rejects_non_positive_qty() -> None:
    with pytest.raises(ValueError, match="qty must be > 0"):
        build_management_order(
            signal_id="s", symbol="AAPL", side="buy", qty=0, reason="scale_in", seq=1
        )


def test_rejects_unknown_side() -> None:
    with pytest.raises(ValueError, match="side must be"):
        build_management_order(
            signal_id="s", symbol="AAPL", side="hold", qty=1, reason="scale_in", seq=1
        )


def test_rejects_unknown_reason() -> None:
    with pytest.raises(ValueError, match="unknown management reason"):
        build_management_order(
            signal_id="s", symbol="AAPL", side="buy", qty=1, reason="hedge", seq=1
        )


def test_rejects_seq_below_one() -> None:
    with pytest.raises(ValueError, match="seq must be >= 1"):
        build_management_order(
            signal_id="s", symbol="AAPL", side="buy", qty=1, reason="scale_in", seq=0
        )
