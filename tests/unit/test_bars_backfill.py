from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from trident.data.bars import Bar, BarStore
from trident.data.bars_backfill import fill_store


def _bar(symbol: str, minute: int) -> Bar:
    return Bar(
        symbol=symbol,
        ts=datetime(2026, 5, 14, 14, minute, tzinfo=UTC),
        timeframe="1min",
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100"),
        volume=10_000,
    )


def test_fill_store_appends_all_bars() -> None:
    store = BarStore()
    bars = [_bar("AAPL", m) for m in range(5)]
    assert fill_store(store, bars) == 5
    assert len(store.recent("AAPL", "1min", 100)) == 5


def test_fill_store_interleaves_symbols_correctly() -> None:
    store = BarStore()
    # Ascending by ts overall; each symbol's subsequence stays ascending.
    bars = [_bar("AAPL", 0), _bar("MSFT", 0), _bar("AAPL", 1), _bar("MSFT", 1)]
    fill_store(store, bars)
    assert len(store.recent("AAPL", "1min", 100)) == 2
    assert len(store.recent("MSFT", "1min", 100)) == 2


def test_fill_store_is_idempotent_on_a_re_backfill() -> None:
    store = BarStore()
    bars = [_bar("AAPL", m) for m in range(3)]
    fill_store(store, bars)
    # Re-feeding the same bars must not duplicate — the store drops non-newer bars.
    fill_store(store, bars)
    assert len(store.recent("AAPL", "1min", 100)) == 3


def test_fill_store_empty_is_a_noop() -> None:
    store = BarStore()
    assert fill_store(store, []) == 0
    assert store.recent("AAPL", "1min", 100) == []
