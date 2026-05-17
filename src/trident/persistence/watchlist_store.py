"""DB read/write helpers for the watchlists table.

``normalize_symbols`` is a pure utility; the rest touch the DB via
``session_scope``. The risk gate never calls these directly — callers feed the
resolved symbol list into the gate as plain data.
"""
from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select, update

from trident.persistence.models import Watchlist
from trident.persistence.session import session_scope

VALID_SOURCES: frozenset[str] = frozenset({"static", "manual", "screener"})


@dataclass(frozen=True)
class WatchlistRecord:
    """Active watchlist row as a plain value object, decoupled from the ORM."""

    id: uuid.UUID
    symbols: list[str]
    source: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


def normalize_symbols(symbols: Sequence[str]) -> list[str]:
    """Uppercase, strip whitespace, dedupe (stable insertion order), drop blanks."""
    seen: set[str] = set()
    result: list[str] = []
    for s in symbols:
        cleaned = s.strip().upper()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            result.append(cleaned)
    return result


def get_active_watchlist() -> WatchlistRecord | None:
    """Return the most-recently-activated watchlist row, or None if none is active."""
    with session_scope() as s:
        row = s.scalars(
            select(Watchlist)
            .where(Watchlist.is_active.is_(True))
            .order_by(Watchlist.created_at.desc())
            .limit(1)
        ).first()
        if row is None:
            return None
        return _row_to_record(row)


def set_watchlist(symbols: Sequence[str], source: str) -> uuid.UUID:
    """Replace the active watchlist atomically.

    Deactivates every currently-active row, then inserts a new active row in
    the same transaction. Returns the new row's UUID.

    Raises ``ValueError`` for an unknown source or an empty symbol list.
    """
    if source not in VALID_SOURCES:
        raise ValueError(
            f"Invalid source {source!r}; must be one of {sorted(VALID_SOURCES)}"
        )
    cleaned = normalize_symbols(symbols)
    if not cleaned:
        raise ValueError("watchlist must contain at least one symbol after normalization")

    now = datetime.now(UTC)
    new_id = uuid.uuid4()
    with session_scope() as s:
        s.execute(
            update(Watchlist)
            .where(Watchlist.is_active.is_(True))
            .values(is_active=False, updated_at=now)
        )
        s.add(
            Watchlist(
                id=new_id,
                symbols=cleaned,
                source=source,
                is_active=True,
                created_at=now,
                updated_at=now,
            )
        )
    from trident.audit.log import record

    record(
        "watchlist_updated",
        actor="watchlist_store",
        payload={"id": str(new_id), "source": source, "symbols": cleaned},
    )
    return new_id


def get_all_watchlists() -> list[WatchlistRecord]:
    """All rows ordered newest-first (for the dashboard history view)."""
    with session_scope() as s:
        rows = list(s.scalars(select(Watchlist).order_by(Watchlist.created_at.desc())))
        return [_row_to_record(r) for r in rows]


def _row_to_record(row: Watchlist) -> WatchlistRecord:
    return WatchlistRecord(
        id=row.id,
        symbols=list(row.symbols),
        source=row.source,
        is_active=row.is_active,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
