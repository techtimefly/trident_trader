from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from trident.backtest.engine import run_day
from trident.clock import ET
from trident.data.bars import Bar
from trident.risk.limits import RiskLimits

DAY = date(2026, 5, 14)
EQUITY = Decimal("100000")


def _bar(hour: int, minute: int, high: str, low: str, close: str, volume: int) -> Bar:
    ts = datetime(2026, 5, 14, hour, minute, tzinfo=ET).astimezone(UTC)
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


def _orb_day_bars() -> list[Bar]:
    """A day of AAPL bars that yields exactly one ORB long trade (target hit)."""
    bars = [_bar(9, 30 + i, "101.0", "99.0", "100.0", 10_000) for i in range(5)]
    # Breakout: closes above the OR high (101.0) with >=1.5x avg OR volume.
    bars.append(_bar(9, 35, "101.6", "101.0", "101.50", 50_000))
    # Follow-up bar that tags the 1R target (entry 101.50 + 2.50 = 104.00).
    bars.append(_bar(9, 40, "104.5", "101.5", "104.20", 30_000))
    return bars


def test_run_day_default_strategy_is_orb() -> None:
    trades = run_day(DAY, _orb_day_bars(), EQUITY, RiskLimits(), ["AAPL"])
    assert len(trades) == 1
    assert trades[0].signal.strategy == "orb_5m"


def test_run_day_explicit_orb_matches_default() -> None:
    # The registry seam must not change ORB behaviour: an explicit strategy_name
    # of "orb_5m" produces byte-identical trades to the default.
    bars = _orb_day_bars()
    default = run_day(DAY, bars, EQUITY, RiskLimits(), ["AAPL"])
    explicit = run_day(DAY, bars, EQUITY, RiskLimits(), ["AAPL"], strategy_name="orb_5m")
    assert default == explicit
    assert len(explicit) == 1


def test_run_day_unknown_strategy_raises() -> None:
    with pytest.raises(ValueError, match="Unknown strategy"):
        run_day(DAY, _orb_day_bars(), EQUITY, RiskLimits(), ["AAPL"], strategy_name="bogus")
