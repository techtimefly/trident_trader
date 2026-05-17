from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from trident.backtest.compare import compare_strategies
from trident.clock import ET
from trident.data.bars import Bar
from trident.risk.limits import RiskLimits

EQUITY = Decimal("100000")


def _bar(
    day: date, hour: int, minute: int, high: str, low: str, close: str, volume: int
) -> Bar:
    ts = datetime(day.year, day.month, day.day, hour, minute, tzinfo=ET).astimezone(UTC)
    return Bar(
        symbol="AAPL",
        ts=ts,
        timeframe="1min",
        open=Decimal(close),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=volume,
    )


def _orb_day(day: date) -> list[Bar]:
    """One day of AAPL bars that yields exactly one ORB long trade (target hit)."""
    bars = [_bar(day, 9, 30 + i, "101.0", "99.0", "100.0", 10_000) for i in range(5)]
    bars.append(_bar(day, 9, 35, "101.6", "101.0", "101.50", 50_000))
    bars.append(_bar(day, 9, 40, "104.5", "101.5", "104.20", 30_000))
    return bars


def test_compare_returns_one_trade_list_per_strategy() -> None:
    bars_by_day = {date(2026, 5, 14): _orb_day(date(2026, 5, 14))}
    result = compare_strategies(["orb_5m"], bars_by_day, EQUITY, RiskLimits(), ["AAPL"])
    assert set(result) == {"orb_5m"}
    assert len(result["orb_5m"]) == 1


def test_compare_preserves_strategy_name_order() -> None:
    bars_by_day = {date(2026, 5, 14): _orb_day(date(2026, 5, 14))}
    result = compare_strategies(["orb_5m"], bars_by_day, EQUITY, RiskLimits(), ["AAPL"])
    assert list(result) == ["orb_5m"]


def test_compare_runs_every_day() -> None:
    days = [date(2026, 5, 14), date(2026, 5, 15)]
    bars_by_day = {d: _orb_day(d) for d in days}
    result = compare_strategies(["orb_5m"], bars_by_day, EQUITY, RiskLimits(), ["AAPL"])
    # One ORB trade per day — a fresh strategy instance per run_day call.
    assert len(result["orb_5m"]) == 2


def test_compare_empty_strategy_list_gives_empty_result() -> None:
    bars_by_day = {date(2026, 5, 14): _orb_day(date(2026, 5, 14))}
    assert compare_strategies([], bars_by_day, EQUITY, RiskLimits(), ["AAPL"]) == {}


def test_compare_unknown_strategy_raises() -> None:
    bars_by_day = {date(2026, 5, 14): _orb_day(date(2026, 5, 14))}
    with pytest.raises(ValueError, match="Unknown strategy"):
        compare_strategies(["bogus"], bars_by_day, EQUITY, RiskLimits(), ["AAPL"])
