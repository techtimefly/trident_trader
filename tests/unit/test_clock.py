from __future__ import annotations

from datetime import date, datetime

from trident.clock import (
    ET,
    current_session,
    is_market_open,
    is_trading_day,
    minutes_since_open,
    minutes_until_close,
    next_trading_day,
    session_for,
)


def test_weekend_is_not_a_trading_day() -> None:
    assert not is_trading_day(date(2026, 5, 16))  # Saturday
    assert not is_trading_day(date(2026, 5, 17))  # Sunday


def test_holiday_is_not_a_trading_day() -> None:
    assert not is_trading_day(date(2026, 1, 1))
    assert not is_trading_day(date(2026, 11, 26))  # Thanksgiving


def test_regular_session_window() -> None:
    sess = session_for(date(2026, 5, 14))
    assert sess is not None
    assert sess.open_at.hour == 9 and sess.open_at.minute == 30
    assert sess.close_at.hour == 16 and sess.close_at.minute == 0
    assert sess.is_early_close is False


def test_early_close_session_window() -> None:
    sess = session_for(date(2026, 11, 27))
    assert sess is not None
    assert sess.close_at.hour == 13
    assert sess.is_early_close is True


def test_no_session_on_holiday() -> None:
    assert session_for(date(2026, 7, 3)) is None


def test_market_open_at_10am_et() -> None:
    at = datetime(2026, 5, 14, 10, 0, tzinfo=ET)
    assert is_market_open(at) is True


def test_market_closed_at_4pm_et() -> None:
    at = datetime(2026, 5, 14, 16, 0, tzinfo=ET)
    assert is_market_open(at) is False


def test_market_closed_at_8am_et() -> None:
    at = datetime(2026, 5, 14, 8, 0, tzinfo=ET)
    assert is_market_open(at) is False


def test_minutes_since_open_returns_none_before_open() -> None:
    at = datetime(2026, 5, 14, 8, 0, tzinfo=ET)
    assert minutes_since_open(at) is None


def test_minutes_since_open_at_10am() -> None:
    at = datetime(2026, 5, 14, 10, 0, tzinfo=ET)
    assert minutes_since_open(at) == 30


def test_minutes_until_close_at_3_55pm() -> None:
    at = datetime(2026, 5, 14, 15, 55, tzinfo=ET)
    assert minutes_until_close(at) == 5


def test_next_trading_day_skips_weekend() -> None:
    # 2026-05-15 is a Friday → next is Monday 2026-05-18
    assert next_trading_day(date(2026, 5, 15)) == date(2026, 5, 18)


def test_next_trading_day_skips_holiday() -> None:
    # 2026-12-24 (Thursday, early close) → next is 2026-12-28 (Mon)
    # since 12-25 is Christmas (closed), 12-26 Sat, 12-27 Sun.
    assert next_trading_day(date(2026, 12, 24)) == date(2026, 12, 28)


def test_current_session_uses_provided_time() -> None:
    at = datetime(2026, 5, 14, 10, 0, tzinfo=ET)
    sess = current_session(at)
    assert sess is not None
    assert sess.trading_day == date(2026, 5, 14)
