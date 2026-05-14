from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from trident.data.bars import Bar, BarStore


def make_bar(symbol: str, minute: int, close: str = "100.0") -> Bar:
    return Bar(
        symbol=symbol,
        ts=datetime(2026, 5, 14, 14, minute, tzinfo=UTC),
        timeframe="1min",
        open=Decimal(close),
        high=Decimal(close),
        low=Decimal(close),
        close=Decimal(close),
        volume=10_000,
    )


def test_store_appends_and_returns_latest() -> None:
    store = BarStore()
    b1 = make_bar("AAPL", 30)
    b2 = make_bar("AAPL", 31)
    store.append(b1)
    store.append(b2)
    assert store.latest("AAPL", "1min") == b2


def test_store_ignores_out_of_order_bars() -> None:
    store = BarStore()
    store.append(make_bar("AAPL", 31))
    store.append(make_bar("AAPL", 30))  # earlier — must be ignored
    assert store.latest("AAPL", "1min").ts.minute == 31


def test_store_recent_returns_last_n() -> None:
    store = BarStore()
    for m in range(30, 40):
        store.append(make_bar("AAPL", m))
    recent = store.recent("AAPL", "1min", 3)
    assert [b.ts.minute for b in recent] == [37, 38, 39]


def test_store_bars_between() -> None:
    store = BarStore()
    for m in range(30, 40):
        store.append(make_bar("AAPL", m))
    start = datetime(2026, 5, 14, 14, 33, tzinfo=UTC)
    end = datetime(2026, 5, 14, 14, 35, tzinfo=UTC)
    found = store.bars_between("AAPL", "1min", start, end)
    assert [b.ts.minute for b in found] == [33, 34, 35]


def test_store_ring_buffer_eviction() -> None:
    store = BarStore(maxlen=3)
    for m in range(30, 36):
        store.append(make_bar("AAPL", m))
    recent = store.recent("AAPL", "1min", 10)
    assert [b.ts.minute for b in recent] == [33, 34, 35]
