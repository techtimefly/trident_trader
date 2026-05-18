"""Unit tests for the pure screen-preset helpers.

Covers the criteria <-> JSON converters (round-trip fidelity) and the
``resolve_screen_criteria`` fallback — all without a database, with the DB
lookup monkeypatched.
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trident.persistence.screen_presets_store import (
    ScreenPresetRecord,
    criteria_from_json,
    criteria_to_json,
)
from trident.screener.criteria import ScreenCriteria
from trident.screener.presets import (
    DEFAULT_CRITERIA,
    DEFAULT_LOOKBACK_DAYS,
    resolve_screen_criteria,
)

_ACTIVE_PRESET = "trident.persistence.screen_presets_store.get_active_preset"


# --------------------------------------------------------------------------
# criteria_to_json / criteria_from_json — pure round-trip
# --------------------------------------------------------------------------


def _roundtrip(c: ScreenCriteria) -> ScreenCriteria:
    return criteria_from_json(criteria_to_json(c))


def test_roundtrip_full_criteria() -> None:
    c = ScreenCriteria(
        min_price=Decimal("1.50"),
        max_price=Decimal("99.99"),
        min_avg_volume=750_000,
        min_change_pct=Decimal("-2.5"),
        max_change_pct=Decimal("12.0"),
        min_market_cap=1_000_000_000,
        max_market_cap=50_000_000_000,
        sectors=("Technology", "Healthcare"),
        exchanges=("NASDAQ",),
    )
    assert _roundtrip(c) == c


def test_roundtrip_empty_criteria() -> None:
    assert _roundtrip(ScreenCriteria()) == ScreenCriteria()


def test_roundtrip_partial_criteria() -> None:
    c = ScreenCriteria(max_price=Decimal("1.00"), min_avg_volume=2_000_000)
    assert _roundtrip(c) == c


def test_to_json_is_json_serializable() -> None:
    """The serialized form must be plain JSON — no Decimal, no tuple."""
    c = ScreenCriteria(min_price=Decimal("3.33"), sectors=("Energy",))
    dumped = json.dumps(criteria_to_json(c))  # would raise on a Decimal/tuple
    assert "3.33" in dumped
    assert "Energy" in dumped


def test_from_json_tolerates_missing_keys() -> None:
    """An older serialized preset (fewer keys) still loads."""
    crit = criteria_from_json({"min_price": "2.00"})
    assert crit.min_price == Decimal("2.00")
    assert crit.max_price is None
    assert crit.sectors == ()
    assert crit.exchanges == ()


# --------------------------------------------------------------------------
# resolve_screen_criteria — fallback behaviour
# --------------------------------------------------------------------------


def test_resolve_falls_back_when_no_active_preset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_ACTIVE_PRESET, lambda: None)
    criteria, lookback = resolve_screen_criteria()
    assert criteria == DEFAULT_CRITERIA
    assert lookback == DEFAULT_LOOKBACK_DAYS


def test_resolve_falls_back_on_db_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom() -> object:
        raise RuntimeError("db down")

    monkeypatch.setattr(_ACTIVE_PRESET, _boom)
    criteria, lookback = resolve_screen_criteria()
    assert criteria == DEFAULT_CRITERIA
    assert lookback == DEFAULT_LOOKBACK_DAYS


def test_resolve_returns_active_preset(monkeypatch: pytest.MonkeyPatch) -> None:
    wanted = ScreenCriteria(max_price=Decimal("5"), sectors=("Energy",))
    now = datetime.now(UTC)
    record = ScreenPresetRecord(
        id=uuid.uuid4(),
        name="Energy small-caps",
        criteria=wanted,
        lookback_days=30,
        source="manual",
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    monkeypatch.setattr(_ACTIVE_PRESET, lambda: record)
    criteria, lookback = resolve_screen_criteria()
    assert criteria == wanted
    assert lookback == 30
