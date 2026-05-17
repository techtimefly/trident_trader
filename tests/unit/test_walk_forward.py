from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from trident.backtest.simulator import SimulatedTrade
from trident.backtest.walk_forward import walk_forward
from trident.strategies.base import Signal

DAYS = [
    date(2026, 5, 4),
    date(2026, 5, 5),
    date(2026, 5, 6),
    date(2026, 5, 7),
    date(2026, 5, 8),
]


def _trade_on(day: date) -> SimulatedTrade:
    # 14:00 UTC is ~10:00 ET — same calendar date in ET, where windows bucket trades.
    sig = Signal(
        ts=datetime(day.year, day.month, day.day, 14, 0, tzinfo=UTC),
        strategy="orb_5m",
        symbol="AAPL",
        side="long",
        entry_price=Decimal("100"),
        stop_price=Decimal("99"),
        target_price=Decimal("102"),
    )
    return SimulatedTrade(
        signal=sig,
        qty=1,
        entry_price=Decimal("100"),
        exit_reason="target",
        exit_price=Decimal("102"),
        exit_ts_iso="2026-05-13T13:40:00+00:00",
        pnl=Decimal("2"),
        gross_pnl=Decimal("2"),
        ideal_entry_price=Decimal("100"),
    )


def test_no_trading_days_yields_no_windows() -> None:
    assert walk_forward([], [], 5) == []
    assert walk_forward([_trade_on(date(2026, 5, 4))], [], 5) == []


def test_invalid_window_days_raises() -> None:
    with pytest.raises(ValueError):
        walk_forward([], DAYS, 0)


def test_chunks_into_consecutive_windows() -> None:
    # 5 days, window 2 → 3 windows: [5/4, 5/5], [5/6, 5/7], [5/8]
    windows = walk_forward([], DAYS, 2)
    assert len(windows) == 3
    assert [w.index for w in windows] == [0, 1, 2]
    assert windows[0].first_day == date(2026, 5, 4)
    assert windows[0].last_day == date(2026, 5, 5)
    assert windows[0].num_days == 2


def test_partial_final_window_is_kept() -> None:
    windows = walk_forward([], DAYS, 2)
    assert windows[-1].first_day == date(2026, 5, 8)
    assert windows[-1].last_day == date(2026, 5, 8)
    assert windows[-1].num_days == 1


def test_one_window_when_window_exceeds_days() -> None:
    windows = walk_forward([], DAYS, 99)
    assert len(windows) == 1
    assert windows[0].num_days == 5


def test_trades_bucketed_into_their_window() -> None:
    trades = [
        _trade_on(date(2026, 5, 4)),
        _trade_on(date(2026, 5, 7)),
        _trade_on(date(2026, 5, 7)),
    ]
    windows = walk_forward(trades, DAYS, 2)
    assert windows[0].summary.num_trades == 1  # 5/4 trade
    assert windows[1].summary.num_trades == 2  # both 5/7 trades
    assert windows[2].summary.num_trades == 0  # 5/8, no trades
