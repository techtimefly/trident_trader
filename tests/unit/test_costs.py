from __future__ import annotations

from decimal import Decimal

from trident.backtest.costs import (
    ZERO_COST,
    CostModel,
    apply_slippage,
    per_share_fee,
    regulatory_fee,
    round_cents,
)


def test_zero_slippage_is_exact_noop() -> None:
    p = Decimal("123.456")
    for action in ("enter", "exit"):
        for side in ("long", "short"):
            assert apply_slippage(p, side, action, ZERO_COST) == p


def test_long_entry_slips_up() -> None:
    # 100 bps = 1%: a long entry is a buy and fills higher.
    costs = CostModel(slippage_bps=Decimal("100"))
    assert apply_slippage(Decimal("100"), "long", "enter", costs) == Decimal("101.00")


def test_long_exit_slips_down() -> None:
    # A long exit is a sell and fills lower.
    costs = CostModel(slippage_bps=Decimal("100"))
    assert apply_slippage(Decimal("100"), "long", "exit", costs) == Decimal("99.00")


def test_short_entry_slips_down() -> None:
    # A short entry is a sell and fills lower.
    costs = CostModel(slippage_bps=Decimal("100"))
    assert apply_slippage(Decimal("100"), "short", "enter", costs) == Decimal("99.00")


def test_short_exit_slips_up() -> None:
    # A short exit is a buy and fills higher.
    costs = CostModel(slippage_bps=Decimal("100"))
    assert apply_slippage(Decimal("100"), "short", "exit", costs) == Decimal("101.00")


def test_slippage_rounds_to_cents() -> None:
    # 2 bps of 429.865 = 0.085973 → 429.950973 → 429.95
    costs = CostModel(slippage_bps=Decimal("2"))
    assert apply_slippage(Decimal("429.865"), "long", "enter", costs) == Decimal("429.95")


def test_round_cents() -> None:
    assert round_cents(Decimal("1.005")) == Decimal("1.01")
    assert round_cents(Decimal("1.004")) == Decimal("1.00")


def test_per_share_fee_zero_model() -> None:
    assert per_share_fee(100, ZERO_COST) == Decimal("0")


def test_per_share_fee_per_share() -> None:
    costs = CostModel(fee_per_share=Decimal("0.005"))
    assert per_share_fee(200, costs) == Decimal("1.0")


def test_per_share_fee_honors_min_floor() -> None:
    costs = CostModel(fee_per_share=Decimal("0.005"), min_fee=Decimal("1.00"))
    # 10 shares * 0.005 = 0.05 → floored to 1.00
    assert per_share_fee(10, costs) == Decimal("1.00")
    # 1000 shares * 0.005 = 5.00 → above the floor
    assert per_share_fee(1000, costs) == Decimal("5.0")


def test_regulatory_fee_zero_for_buys() -> None:
    costs = CostModel(sec_fee_rate=Decimal("0.0000278"), taf_per_share=Decimal("0.000166"))
    assert regulatory_fee(Decimal("100000"), 100, is_sell=False, costs=costs) == Decimal("0")


def test_regulatory_fee_on_sells() -> None:
    costs = CostModel(sec_fee_rate=Decimal("0.0000278"), taf_per_share=Decimal("0.000166"))
    # SEC: 100000 * 0.0000278 = 2.78 ; TAF: 100 * 0.000166 = 0.0166
    fee = regulatory_fee(Decimal("100000"), 100, is_sell=True, costs=costs)
    assert fee == Decimal("2.78") + Decimal("0.0166")
