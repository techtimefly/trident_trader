from __future__ import annotations

from datetime import datetime

from trident.clock import ET
from trident.safety.eod_flatten import FLATTEN_OFFSET, is_past_flatten_for


def test_not_past_flatten_at_3_30_pm_et() -> None:
    at = datetime(2026, 5, 15, 15, 30, tzinfo=ET)
    assert is_past_flatten_for(at) is False


def test_past_flatten_at_3_55_pm_et() -> None:
    at = datetime(2026, 5, 15, 15, 55, tzinfo=ET)
    assert is_past_flatten_for(at) is True


def test_past_flatten_at_3_54_59_pm_et() -> None:
    at = datetime(2026, 5, 15, 15, 54, 59, tzinfo=ET)
    assert is_past_flatten_for(at) is False


def test_past_flatten_on_early_close_at_12_55_pm_et() -> None:
    # 2026-11-27 is the day after Thanksgiving — early close at 13:00 ET.
    at = datetime(2026, 11, 27, 12, 55, tzinfo=ET)
    assert is_past_flatten_for(at) is True


def test_not_past_flatten_on_early_close_at_12_30_pm_et() -> None:
    at = datetime(2026, 11, 27, 12, 30, tzinfo=ET)
    assert is_past_flatten_for(at) is False


def test_treats_holiday_as_past_flatten() -> None:
    # On a non-trading day there's nothing to do; flatten is "past" by definition.
    at = datetime(2026, 7, 3, 10, 0, tzinfo=ET)
    assert is_past_flatten_for(at) is True


def test_flatten_offset_is_five_minutes() -> None:
    # The runner schedules `flatten_now` at session_close - FLATTEN_OFFSET; if this
    # constant changes, every test using a 15:55 boundary changes. Pin it.
    assert FLATTEN_OFFSET.total_seconds() == 300
