from __future__ import annotations

from unittest.mock import MagicMock, patch

from trident.watchlist import WATCHLIST, resolve_watchlist

# ---------------------------------------------------------------------------
# WATCHLIST constant
# ---------------------------------------------------------------------------


def test_watchlist_is_non_empty_uppercase_strings() -> None:
    assert WATCHLIST
    assert all(isinstance(s, str) and s == s.upper() for s in WATCHLIST)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_store(symbols: list[str] | None = None, raises: Exception | None = None) -> MagicMock:
    """Return a fake watchlist_store module for sys.modules patching."""
    store = MagicMock()
    if raises is not None:
        store.get_active_watchlist.side_effect = raises
    elif symbols is None:
        store.get_active_watchlist.return_value = None
    else:
        record = MagicMock()
        record.symbols = symbols
        store.get_active_watchlist.return_value = record
    return store


def _resolve_with(store: MagicMock) -> list[str]:
    with patch.dict("sys.modules", {"trident.persistence.watchlist_store": store}):
        return resolve_watchlist()


# ---------------------------------------------------------------------------
# resolve_watchlist — DB returns an active row
# ---------------------------------------------------------------------------


def test_resolve_watchlist_uses_db_symbols_when_active_row_exists() -> None:
    result = _resolve_with(_mock_store(["TSLA", "AMZN"]))
    assert result == ["TSLA", "AMZN"]


def test_resolve_watchlist_returns_fresh_copy_of_db_symbols() -> None:
    store = _mock_store(["TSLA", "AMZN"])
    result1 = _resolve_with(store)
    result1.append("ZZZZ")
    result2 = _resolve_with(store)
    assert "ZZZZ" not in result2


# ---------------------------------------------------------------------------
# resolve_watchlist — DB returns None (no active row) → static fallback
# ---------------------------------------------------------------------------


def test_resolve_watchlist_falls_back_to_constant_when_db_returns_none() -> None:
    result = _resolve_with(_mock_store(symbols=None))
    assert result == WATCHLIST


def test_resolve_watchlist_fallback_is_fresh_copy() -> None:
    store = _mock_store(symbols=None)
    resolved = _resolve_with(store)
    resolved.append("ZZZZ")
    assert "ZZZZ" not in WATCHLIST
    assert _resolve_with(store) == WATCHLIST


# ---------------------------------------------------------------------------
# resolve_watchlist — DB returns row with empty symbols → static fallback
# ---------------------------------------------------------------------------


def test_resolve_watchlist_falls_back_when_active_row_has_empty_symbols() -> None:
    result = _resolve_with(_mock_store(symbols=[]))
    assert result == WATCHLIST


# ---------------------------------------------------------------------------
# resolve_watchlist — DB raises → static fallback, never empty
# ---------------------------------------------------------------------------


def test_resolve_watchlist_falls_back_when_db_raises() -> None:
    result = _resolve_with(_mock_store(raises=RuntimeError("connection refused")))
    assert result == WATCHLIST


def test_resolve_watchlist_never_returns_empty_list() -> None:
    """Invariant: resolve_watchlist() is non-empty regardless of DB state."""
    cases: list[MagicMock] = [
        _mock_store(symbols=None),
        _mock_store(symbols=[]),
        _mock_store(raises=RuntimeError("db down")),
    ]
    for store in cases:
        assert len(_resolve_with(store)) > 0
