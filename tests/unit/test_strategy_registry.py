from __future__ import annotations

import pytest

from trident.strategies.orb import OpeningRangeBreakout
from trident.strategies.registry import (
    available_strategies,
    build_strategy,
    register,
)


def test_orb_is_registered() -> None:
    assert "orb_5m" in available_strategies()


def test_build_strategy_returns_the_right_type() -> None:
    strat = build_strategy("orb_5m", ["AAPL"])
    assert isinstance(strat, OpeningRangeBreakout)
    assert strat.name == "orb_5m"


def test_build_strategy_returns_a_fresh_instance_each_call() -> None:
    # Strategies hold mutable per-day state; two builds must not alias.
    a = build_strategy("orb_5m", ["AAPL"])
    b = build_strategy("orb_5m", ["AAPL"])
    assert a is not b


def test_build_strategy_forwards_symbols_to_the_constructor() -> None:
    strat = build_strategy("orb_5m", ["aapl", "msft"])
    assert isinstance(strat, OpeningRangeBreakout)
    assert strat._symbols == {"AAPL", "MSFT"}


def test_build_strategy_unknown_name_raises_listing_available() -> None:
    with pytest.raises(ValueError, match="Unknown strategy 'nope'") as exc:
        build_strategy("nope", ["AAPL"])
    assert "orb_5m" in str(exc.value)


def test_register_rejects_duplicate_name() -> None:
    with pytest.raises(ValueError, match="already registered"):
        register("orb_5m", lambda symbols: OpeningRangeBreakout(symbols=symbols))


def test_available_strategies_is_sorted() -> None:
    names = available_strategies()
    assert names == sorted(names)
