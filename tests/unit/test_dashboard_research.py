"""Unit tests for pure helper functions added in the research / watchlist-quality phases.

These tests exercise logic that is decoupled from DB / network:
 - _parse_signal_date: date parsing + default fallback
 - _symbol_perf_context / _backtest_history_context error paths (load_error flag)
 - Backtest-trigger validation (days / strategy / window_days bounds)
"""
from __future__ import annotations

from datetime import date
from unittest.mock import patch

# ---------------------------------------------------------------------------
# _parse_signal_date
# ---------------------------------------------------------------------------

def _import_parse_signal_date():
    from trident.dashboard.app import _parse_signal_date
    return _parse_signal_date


def test_parse_signal_date_valid_iso() -> None:
    fn = _import_parse_signal_date()
    assert fn("2026-01-15") == date(2026, 1, 15)


def test_parse_signal_date_none_returns_today() -> None:
    fn = _import_parse_signal_date()
    result = fn(None)
    # Should be today's date (ET); just verify it's a date object
    assert isinstance(result, date)


def test_parse_signal_date_invalid_falls_back_to_today() -> None:
    fn = _import_parse_signal_date()
    result = fn("not-a-date")
    assert isinstance(result, date)


def test_parse_signal_date_strips_whitespace() -> None:
    fn = _import_parse_signal_date()
    assert fn("  2026-03-20  ") == date(2026, 3, 20)


# ---------------------------------------------------------------------------
# Backtest trigger input validation (reproduces the form-parsing logic)
# ---------------------------------------------------------------------------

def _validate_backtest_form(
    days_raw: str,
    strategy_raw: str,
    window_raw: str,
    valid_strategies: list[str],
) -> tuple[str | None, bool]:
    """Replica of the validation logic inside api_backtest_run."""
    try:
        days = int(days_raw.strip())
        window_days = int(window_raw.strip())
        strategy = strategy_raw.strip()
        if days < 1 or days > 500:
            raise ValueError("days out of range")
        if window_days < 1 or window_days > 90:
            raise ValueError("window_days out of range")
        if strategy not in valid_strategies:
            raise ValueError("unknown strategy")
    except (ValueError, ArithmeticError) as exc:
        return str(exc), True
    return None, False


STRATEGIES = ["orb_5m", "vwap_reversion"]


def test_backtest_form_valid() -> None:
    err_msg, is_err = _validate_backtest_form("30", "orb_5m", "5", STRATEGIES)
    assert not is_err
    assert err_msg is None


def test_backtest_form_zero_days() -> None:
    _, is_err = _validate_backtest_form("0", "orb_5m", "5", STRATEGIES)
    assert is_err


def test_backtest_form_too_many_days() -> None:
    _, is_err = _validate_backtest_form("501", "orb_5m", "5", STRATEGIES)
    assert is_err


def test_backtest_form_bad_window() -> None:
    _, is_err = _validate_backtest_form("30", "orb_5m", "91", STRATEGIES)
    assert is_err


def test_backtest_form_unknown_strategy() -> None:
    _, is_err = _validate_backtest_form("30", "not_a_strategy", "5", STRATEGIES)
    assert is_err


def test_backtest_form_non_numeric_days() -> None:
    _, is_err = _validate_backtest_form("abc", "orb_5m", "5", STRATEGIES)
    assert is_err


# ---------------------------------------------------------------------------
# _symbol_perf_context — load_error on DB failure
# ---------------------------------------------------------------------------

def test_symbol_perf_context_load_error_on_exception() -> None:
    from trident.dashboard.app import _symbol_perf_context

    with patch("trident.dashboard.app.session_scope", side_effect=RuntimeError("db down")):
        ctx = _symbol_perf_context()
    assert ctx["load_error"] is True
    assert ctx["rows"] == []


# ---------------------------------------------------------------------------
# _backtest_history_context — load_error on DB failure
# ---------------------------------------------------------------------------

def test_backtest_history_context_load_error_on_exception() -> None:
    from trident.dashboard.app import _backtest_history_context

    with patch("trident.dashboard.app.session_scope", side_effect=RuntimeError("db down")):
        ctx = _backtest_history_context()
    assert ctx["load_error"] is True
    assert ctx["rows"] == []


# ---------------------------------------------------------------------------
# _signal_history_context — load_error on DB failure
# ---------------------------------------------------------------------------

def test_signal_history_context_load_error_on_exception() -> None:
    from trident.dashboard.app import _signal_history_context

    with patch("trident.dashboard.app.session_scope", side_effect=RuntimeError("db down")):
        ctx = _signal_history_context("2026-05-18")
    assert ctx["load_error"] is True
    assert ctx["rows"] == []
    assert ctx["selected_date"] == "2026-05-18"


# ---------------------------------------------------------------------------
# _screen_perf_context — load_error on DB failure
# ---------------------------------------------------------------------------

def test_screen_perf_context_load_error_on_exception() -> None:
    from trident.dashboard.app import _screen_perf_context

    with patch("trident.dashboard.app.get_latest_screen", side_effect=RuntimeError("db down")):
        ctx = _screen_perf_context()
    assert ctx["load_error"] is True


def test_screen_perf_context_empty_when_no_screen() -> None:
    from trident.dashboard.app import _screen_perf_context

    with patch("trident.dashboard.app.get_latest_screen", return_value=None):
        ctx = _screen_perf_context()
    assert ctx["load_error"] is False
    assert ctx["rows"] == []
    assert ctx["summary"] is None
