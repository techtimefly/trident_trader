"""Unit tests for the dashboard's watchlist quote layer.

No network: ``alpaca_view._data_client`` and ``get_settings`` are monkeypatched,
and snapshots are plain ``SimpleNamespace`` stand-ins for the Alpaca SDK objects.
"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from trident.dashboard import alpaca_view


class _FakeSettings:
    """Stand-in for Settings — only the fields the quote layer touches."""

    alpaca_data_feed = "iex"
    alpaca_api_key = "key"
    alpaca_api_secret = "secret"


class _FakeClient:
    """A data client whose ``get_stock_snapshot`` returns a canned map."""

    def __init__(self, snapshots: dict[str, Any]) -> None:
        self._snapshots = snapshots

    def get_stock_snapshot(self, _req: Any) -> dict[str, Any]:
        return self._snapshots


def _trade(price: float | None) -> Any:
    return SimpleNamespace(price=price)


def _bar(close: float | None, volume: int | None = None) -> Any:
    return SimpleNamespace(close=close, volume=volume)


def _quote(bid: float | None, ask: float | None) -> Any:
    return SimpleNamespace(bid_price=bid, ask_price=ask)


def _snapshot(
    trade: Any = None, daily: Any = None, prev: Any = None, quote: Any = None
) -> Any:
    return SimpleNamespace(
        latest_trade=trade, daily_bar=daily, previous_daily_bar=prev, latest_quote=quote
    )


def _patch(monkeypatch: pytest.MonkeyPatch, snapshots: dict[str, Any]) -> None:
    monkeypatch.setattr(alpaca_view, "get_settings", lambda: _FakeSettings())
    monkeypatch.setattr(alpaca_view, "_data_client", lambda: _FakeClient(snapshots))


# --------------------------------------------------------------------------
# get_quotes
# --------------------------------------------------------------------------


def test_normal_snapshot_maps_to_quoteview(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(
        monkeypatch,
        {
            "AAPL": _snapshot(
                trade=_trade(110.0),
                daily=_bar(109.5, 44_200_000),
                prev=_bar(100.0),
                quote=_quote(109.99, 110.01),
            )
        },
    )
    quotes = alpaca_view.get_quotes(["AAPL"])
    q = quotes["AAPL"]
    assert q.last == Decimal("110")
    assert q.change == Decimal("10")
    assert q.change_pct == Decimal("10.00")
    assert q.bid == Decimal("109.99")
    assert q.ask == Decimal("110.01")
    assert q.volume == Decimal("44200000")


def test_falls_back_to_daily_close_when_no_trade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch(monkeypatch, {"MSFT": _snapshot(daily=_bar(50.0, 1_000), prev=_bar(40.0))})
    q = alpaca_view.get_quotes(["MSFT"])["MSFT"]
    assert q.last == Decimal("50")
    assert q.change_pct == Decimal("25.00")


def test_symbol_absent_from_response_is_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch(monkeypatch, {"AAPL": _snapshot(trade=_trade(110.0), prev=_bar(100.0))})
    quotes = alpaca_view.get_quotes(["AAPL", "MSFT"])
    assert set(quotes) == {"AAPL"}


def test_all_none_fields_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, {"NVDA": _snapshot()})
    assert alpaca_view.get_quotes(["NVDA"]) == {}


def test_missing_previous_bar_leaves_change_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch(monkeypatch, {"AMD": _snapshot(trade=_trade(75.0))})
    q = alpaca_view.get_quotes(["AMD"])["AMD"]
    assert q.last == Decimal("75")
    assert q.change is None
    assert q.change_pct is None


def test_zero_bid_ask_kept_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(
        monkeypatch,
        {"SPY": _snapshot(trade=_trade(500.0), prev=_bar(490.0), quote=_quote(0.0, 0.0))},
    )
    q = alpaca_view.get_quotes(["SPY"])["SPY"]
    assert q.bid is None and q.ask is None


def test_empty_symbol_list_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, {})
    assert alpaca_view.get_quotes([]) == {}


def test_total_failure_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(alpaca_view, "get_settings", lambda: _FakeSettings())

    def _boom() -> Any:
        raise RuntimeError("network down")

    monkeypatch.setattr(alpaca_view, "_data_client", _boom)
    assert alpaca_view.get_quotes(["AAPL"]) == {}
