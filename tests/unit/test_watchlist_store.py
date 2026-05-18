"""Unit tests for watchlist_store.

Covers the pure normalization helper, the WatchlistRecord value object, the
VALID_SOURCES constant, the _row_to_record mapper, and the validation guards
that fire before any DB access. The DB-touching paths (create/rename/activate/
delete, add/remove/set symbols, list/get) are exercised by manual verification
against a live database.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from trident.persistence.watchlist_store import (
    VALID_SOURCES,
    WatchlistRecord,
    _row_to_record,
    create_watchlist,
    normalize_symbols,
    rename_watchlist,
)

# ---------------------------------------------------------------------------
# normalize_symbols
# ---------------------------------------------------------------------------


def test_normalize_uppercases() -> None:
    assert normalize_symbols(["aapl", "msft"]) == ["AAPL", "MSFT"]


def test_normalize_strips_whitespace() -> None:
    assert normalize_symbols(["  SPY ", " QQQ"]) == ["SPY", "QQQ"]


def test_normalize_dedupes_stable_order() -> None:
    assert normalize_symbols(["AAPL", "aapl", "MSFT"]) == ["AAPL", "MSFT"]


def test_normalize_drops_blanks() -> None:
    assert normalize_symbols(["AAPL", "", "  ", "MSFT"]) == ["AAPL", "MSFT"]


def test_normalize_empty_input_returns_empty() -> None:
    assert normalize_symbols([]) == []


def test_normalize_preserves_insertion_order() -> None:
    symbols = ["SPY", "QQQ", "AAPL", "MSFT"]
    assert normalize_symbols(symbols) == symbols


def test_normalize_handles_mixed_case_dedup() -> None:
    assert normalize_symbols(["Nvda", "NVDA", "nvda"]) == ["NVDA"]


# ---------------------------------------------------------------------------
# WatchlistRecord
# ---------------------------------------------------------------------------


def _make_record(**kwargs: object) -> WatchlistRecord:
    defaults: dict[str, object] = {
        "id": uuid.uuid4(),
        "name": "Default",
        "symbols": ["AAPL"],
        "source": "manual",
        "is_active": True,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    defaults.update(kwargs)
    return WatchlistRecord(**defaults)  # type: ignore[arg-type]


def test_watchlist_record_is_frozen() -> None:
    r = _make_record()
    with pytest.raises(AttributeError):
        r.symbols = ["MSFT"]  # type: ignore[misc]


def test_watchlist_record_stores_all_fields() -> None:
    now = datetime.now(UTC)
    rid = uuid.uuid4()
    r = WatchlistRecord(
        id=rid,
        name="Momentum",
        symbols=["SPY", "QQQ"],
        source="screener",
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    assert r.id == rid
    assert r.name == "Momentum"
    assert r.symbols == ["SPY", "QQQ"]
    assert r.source == "screener"
    assert r.is_active is True
    assert r.created_at == now


# ---------------------------------------------------------------------------
# VALID_SOURCES
# ---------------------------------------------------------------------------


def test_valid_sources_contains_expected() -> None:
    assert frozenset({"static", "manual", "screener"}) == VALID_SOURCES


# ---------------------------------------------------------------------------
# Validation guards — raise before touching the DB
# ---------------------------------------------------------------------------


def test_create_watchlist_blank_name_raises() -> None:
    with pytest.raises(ValueError, match="must not be blank"):
        create_watchlist("   ")


def test_create_watchlist_invalid_source_raises() -> None:
    with pytest.raises(ValueError, match="Invalid source"):
        create_watchlist("Tech", source="bad_source")


def test_create_watchlist_unknown_source_lists_valid_sources() -> None:
    with pytest.raises(ValueError) as exc:
        create_watchlist("Tech", source="live")
    msg = str(exc.value)
    assert "manual" in msg
    assert "screener" in msg
    assert "static" in msg


def test_rename_watchlist_blank_name_raises() -> None:
    with pytest.raises(ValueError, match="must not be blank"):
        rename_watchlist(uuid.uuid4(), "   ")


# ---------------------------------------------------------------------------
# _row_to_record — maps an ORM row onto the plain value object
# ---------------------------------------------------------------------------


def test_row_to_record_copies_all_fields() -> None:
    now = datetime.now(UTC)
    rid = uuid.uuid4()
    row = MagicMock()
    row.id = rid
    row.name = "Default"
    row.symbols = ["AAPL", "MSFT"]
    row.source = "manual"
    row.is_active = True
    row.created_at = now
    row.updated_at = now

    r = _row_to_record(row)

    assert r.id == rid
    assert r.name == "Default"
    assert r.symbols == ["AAPL", "MSFT"]
    assert r.source == "manual"
    assert r.is_active is True
    assert r.created_at == now
    assert r.updated_at == now


def test_row_to_record_returns_copy_of_symbols_list() -> None:
    now = datetime.now(UTC)
    row = MagicMock()
    row.id = uuid.uuid4()
    row.name = "Default"
    row.symbols = ["AAPL"]
    row.source = "static"
    row.is_active = False
    row.created_at = now
    row.updated_at = now

    r = _row_to_record(row)
    r.symbols.append("MSFT")  # mutate the copy
    assert row.symbols == ["AAPL"]  # original untouched
