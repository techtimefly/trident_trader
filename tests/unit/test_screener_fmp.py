"""Unit tests for the pure parts of the FMP screener data layer.

No network: ``build_screener_params`` and ``parse_screener_response`` are pure,
and ``fetch_fmp_universe`` is exercised only on its degradation paths with
``httpx.get`` / ``get_settings`` monkeypatched.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from trident.screener import fmp
from trident.screener.criteria import ScreenCriteria


class _FakeSettings:
    """Stand-in for Settings — only the field the FMP layer reads."""

    def __init__(self, fmp_api_key: str = "") -> None:
        self.fmp_api_key = fmp_api_key


# --------------------------------------------------------------------------
# is_configured
# --------------------------------------------------------------------------


def test_is_configured_false_when_blank(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fmp, "get_settings", lambda: _FakeSettings(""))
    assert fmp.is_configured() is False


def test_is_configured_false_when_whitespace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fmp, "get_settings", lambda: _FakeSettings("   "))
    assert fmp.is_configured() is False


def test_is_configured_true_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fmp, "get_settings", lambda: _FakeSettings("abc123"))
    assert fmp.is_configured() is True


# --------------------------------------------------------------------------
# build_screener_params — pure
# --------------------------------------------------------------------------


def test_build_params_empty_criteria_is_minimal() -> None:
    params = fmp.build_screener_params(ScreenCriteria(), max_symbols=300)
    assert params == {"country": "US", "isActivelyTrading": "true", "limit": "300"}


def test_build_params_omits_change_pct() -> None:
    """FMP's screener has no % change parameter — those bounds must not leak."""
    crit = ScreenCriteria(min_change_pct=Decimal("5"), max_change_pct=Decimal("20"))
    params = fmp.build_screener_params(crit, max_symbols=100)
    assert params == {"country": "US", "isActivelyTrading": "true", "limit": "100"}


def test_build_params_widens_price_band() -> None:
    crit = ScreenCriteria(min_price=Decimal("10"), max_price=Decimal("100"))
    params = fmp.build_screener_params(crit, max_symbols=500)
    # Lower bound widened down 10%, upper bound widened up 10% (loose pre-filter).
    assert params["priceMoreThan"] == "9.00"
    assert params["priceLowerThan"] == "110.00"


def test_build_params_uses_loose_volume_floor() -> None:
    crit = ScreenCriteria(min_avg_volume=1_000_000)
    params = fmp.build_screener_params(crit, max_symbols=500)
    assert params["volumeMoreThan"] == "200000"  # 20% of 1,000,000


def test_build_params_market_cap_passthrough() -> None:
    crit = ScreenCriteria(min_market_cap=1_000_000_000, max_market_cap=5_000_000_000)
    params = fmp.build_screener_params(crit, max_symbols=500)
    assert params["marketCapMoreThan"] == "1000000000"
    assert params["marketCapLowerThan"] == "5000000000"


def test_build_params_single_sector_sent_multi_omitted() -> None:
    one = fmp.build_screener_params(ScreenCriteria(sectors=("Technology",)), max_symbols=500)
    assert one["sector"] == "Technology"
    many = fmp.build_screener_params(
        ScreenCriteria(sectors=("Technology", "Energy")), max_symbols=500
    )
    assert "sector" not in many


def test_build_params_single_exchange_sent_multi_omitted() -> None:
    one = fmp.build_screener_params(ScreenCriteria(exchanges=("NASDAQ",)), max_symbols=500)
    assert one["exchange"] == "NASDAQ"
    many = fmp.build_screener_params(
        ScreenCriteria(exchanges=("NASDAQ", "NYSE")), max_symbols=500
    )
    assert "exchange" not in many


# --------------------------------------------------------------------------
# parse_screener_response — pure
# --------------------------------------------------------------------------


def test_parse_basic_row() -> None:
    payload = [
        {
            "symbol": "AAPL",
            "marketCap": 3_000_000_000_000,
            "sector": "Technology",
            "exchangeShortName": "NASDAQ",
            "price": 195.5,
            "volume": 50_000_000,
        }
    ]
    assets = fmp.parse_screener_response(payload)
    assert len(assets) == 1
    a = assets[0]
    assert a.symbol == "AAPL"
    assert a.market_cap == 3_000_000_000_000
    assert a.sector == "Technology"
    assert a.exchange == "NASDAQ"
    assert a.price == Decimal("195.5")
    assert isinstance(a.price, Decimal)
    assert a.volume == 50_000_000


def test_parse_handles_missing_and_null_fields() -> None:
    payload = [{"symbol": "XYZ"}, {"symbol": "ABC", "marketCap": None, "price": None}]
    assets = fmp.parse_screener_response(payload)
    assert len(assets) == 2
    assert assets[0].market_cap is None
    assert assets[0].price is None
    assert assets[0].sector is None
    assert assets[1].market_cap is None
    assert assets[1].price is None


def test_parse_drops_blank_and_punctuated_symbols() -> None:
    payload = [
        {"symbol": ""},
        {"symbol": "BRK.B"},
        {"symbol": "AB/C"},
        {"symbol": None},
        {"symbol": "GOOD"},
    ]
    assets = fmp.parse_screener_response(payload)
    assert [a.symbol for a in assets] == ["GOOD"]


def test_parse_uppercases_symbol() -> None:
    assets = fmp.parse_screener_response([{"symbol": "aapl"}])
    assert assets[0].symbol == "AAPL"


def test_parse_tolerates_scientific_notation_market_cap() -> None:
    assets = fmp.parse_screener_response([{"symbol": "AAA", "marketCap": 1.39e12}])
    assert assets[0].market_cap == 1_390_000_000_000


def test_parse_empty_list() -> None:
    assert fmp.parse_screener_response([]) == []


# --------------------------------------------------------------------------
# fetch_fmp_universe — degradation paths only (no real network)
# --------------------------------------------------------------------------


def test_fetch_returns_empty_when_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fmp, "get_settings", lambda: _FakeSettings(""))
    assert fmp.fetch_fmp_universe(ScreenCriteria()) == []


def test_fetch_returns_empty_on_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fmp, "get_settings", lambda: _FakeSettings("a-key"))

    def _boom(*args: object, **kwargs: object) -> object:
        raise RuntimeError("network down")

    monkeypatch.setattr(fmp.httpx, "get", _boom)
    # Outer-ring: degrades to [], never raises.
    assert fmp.fetch_fmp_universe(ScreenCriteria()) == []


class _FakeResponse:
    """Minimal stand-in for an httpx.Response (non-200 path)."""

    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


def test_fetch_returns_empty_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 402/403 (paywalled or legacy endpoint) degrades to [], never raises."""
    monkeypatch.setattr(fmp, "get_settings", lambda: _FakeSettings("a-key"))
    monkeypatch.setattr(
        fmp.httpx,
        "get",
        lambda *a, **k: _FakeResponse(402, "Restricted Endpoint"),
    )
    assert fmp.fetch_fmp_universe(ScreenCriteria()) == []
