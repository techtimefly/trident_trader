from __future__ import annotations

from decimal import Decimal

from trident.risk.sizing import (
    position_fits_buying_power,
    position_notional,
    position_size,
)


def test_position_size_basic() -> None:
    # $50k account, 1% risk, $10 stop distance → $500 / $10 = 50 shares
    shares = position_size(Decimal("50000"), Decimal("1"), Decimal("100"), Decimal("90"))
    assert shares == 50


def test_position_size_floors_fractional() -> None:
    # $10k account, 1% risk, $7 stop distance → $100 / $7 = 14.28… → 14
    shares = position_size(Decimal("10000"), Decimal("1"), Decimal("50"), Decimal("43"))
    assert shares == 14


def test_position_size_zero_stop_distance() -> None:
    assert position_size(Decimal("10000"), Decimal("1"), Decimal("100"), Decimal("100")) == 0


def test_position_size_invalid_inputs() -> None:
    assert position_size(Decimal("0"), Decimal("1"), Decimal("100"), Decimal("90")) == 0
    assert position_size(Decimal("10000"), Decimal("0"), Decimal("100"), Decimal("90")) == 0
    assert position_size(Decimal("10000"), Decimal("1"), Decimal("0"), Decimal("90")) == 0


def test_position_size_short_signal() -> None:
    # For shorts, entry < stop. Sizing only cares about absolute distance.
    shares = position_size(Decimal("50000"), Decimal("1"), Decimal("90"), Decimal("100"))
    assert shares == 50


def test_position_notional() -> None:
    assert position_notional(50, Decimal("100")) == Decimal("5000")


def test_buying_power_check_passes_with_room() -> None:
    assert position_fits_buying_power(
        shares=50, entry_price=Decimal("100"), buying_power=Decimal("10000")
    )


def test_buying_power_check_fails_when_tight() -> None:
    # 50 * 100 = 5000; with 5% buffer needs 5250; buying_power 5100 → fail
    assert not position_fits_buying_power(
        shares=50,
        entry_price=Decimal("100"),
        buying_power=Decimal("5100"),
    )
