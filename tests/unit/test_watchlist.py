from __future__ import annotations

from trident.watchlist import WATCHLIST, resolve_watchlist


def test_watchlist_is_non_empty_uppercase_strings() -> None:
    assert WATCHLIST
    assert all(isinstance(s, str) and s == s.upper() for s in WATCHLIST)


def test_resolve_watchlist_matches_the_constant() -> None:
    assert resolve_watchlist() == WATCHLIST


def test_resolve_watchlist_returns_a_fresh_copy() -> None:
    resolved = resolve_watchlist()
    resolved.append("ZZZZ")
    assert "ZZZZ" not in WATCHLIST
    assert resolve_watchlist() == WATCHLIST
