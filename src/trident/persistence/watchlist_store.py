"""DB read/write helpers for the watchlists table — named, managed watchlists.

``normalize_symbols`` is a pure utility; the rest touch the DB via
``session_scope``. Several named watchlists may coexist; exactly one is active
at a time, and ``trident.watchlist.resolve_watchlist()`` reads that active one.
The runner consumes only the resolved symbol list as plain data — the risk gate
never calls these helpers directly.

Mirrors ``screen_presets_store``: a watchlist is to the runner what an active
screen preset is to the screener.
"""
from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from trident.persistence.models import Watchlist
from trident.persistence.session import session_scope

VALID_SOURCES: frozenset[str] = frozenset({"static", "manual", "screener"})


@dataclass(frozen=True)
class WatchlistRecord:
    """A named watchlist as a plain value object, decoupled from the ORM."""

    id: uuid.UUID
    name: str
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


def list_watchlists() -> list[WatchlistRecord]:
    """Every watchlist, newest-first (for the dashboard list)."""
    with session_scope() as s:
        rows = list(s.scalars(select(Watchlist).order_by(Watchlist.created_at.desc())))
        return [_row_to_record(r) for r in rows]


def get_active_watchlist() -> WatchlistRecord | None:
    """The most-recently-activated watchlist, or None if none is active."""
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


def get_watchlist(watchlist_id: uuid.UUID) -> WatchlistRecord | None:
    """A single watchlist by id, or None if it does not exist."""
    with session_scope() as s:
        row = s.get(Watchlist, watchlist_id)
        return None if row is None else _row_to_record(row)


def create_watchlist(
    name: str, symbols: Sequence[str] = (), source: str = "manual"
) -> uuid.UUID:
    """Create a new named watchlist. Returns its UUID.

    Becomes the active watchlist automatically when no other watchlist is
    active — so the runner has a real list as soon as one exists. Otherwise the
    list is created inactive; activate it explicitly with :func:`activate_watchlist`.

    Raises ``ValueError`` for a blank name, an unknown source, or a name that is
    already taken.
    """
    clean_name = name.strip()
    if not clean_name:
        raise ValueError("watchlist name must not be blank")
    if source not in VALID_SOURCES:
        raise ValueError(
            f"Invalid source {source!r}; must be one of {sorted(VALID_SOURCES)}"
        )
    cleaned = normalize_symbols(symbols)

    now = datetime.now(UTC)
    new_id = uuid.uuid4()
    with session_scope() as s:
        if _name_taken(s, clean_name):
            raise ValueError(f"a watchlist named {clean_name!r} already exists")
        any_active = s.scalars(
            select(Watchlist.id).where(Watchlist.is_active.is_(True)).limit(1)
        ).first()
        s.add(
            Watchlist(
                id=new_id,
                name=clean_name,
                symbols=cleaned,
                source=source,
                is_active=any_active is None,
                created_at=now,
                updated_at=now,
            )
        )
    _audit("watchlist_created", {"id": str(new_id), "name": clean_name, "source": source})
    return new_id


def rename_watchlist(watchlist_id: uuid.UUID, new_name: str) -> None:
    """Rename a watchlist. Raises ``ValueError`` for a blank/taken name or an
    unknown id."""
    clean_name = new_name.strip()
    if not clean_name:
        raise ValueError("watchlist name must not be blank")
    now = datetime.now(UTC)
    with session_scope() as s:
        target = s.get(Watchlist, watchlist_id)
        if target is None:
            raise ValueError(f"no watchlist with id {watchlist_id}")
        if _name_taken(s, clean_name, exclude=watchlist_id):
            raise ValueError(f"a watchlist named {clean_name!r} already exists")
        target.name = clean_name
        target.updated_at = now
    _audit("watchlist_renamed", {"id": str(watchlist_id), "name": clean_name})


def set_watchlist_symbols(watchlist_id: uuid.UUID, symbols: Sequence[str]) -> None:
    """Replace a watchlist's symbols wholesale (normalized). Empty is allowed —
    ``resolve_watchlist()`` falls back to the static constant for an empty
    active list. Raises ``ValueError`` for an unknown id."""
    cleaned = normalize_symbols(symbols)
    now = datetime.now(UTC)
    with session_scope() as s:
        target = s.get(Watchlist, watchlist_id)
        if target is None:
            raise ValueError(f"no watchlist with id {watchlist_id}")
        target.symbols = cleaned
        target.updated_at = now
    _audit("watchlist_symbols_set", {"id": str(watchlist_id), "symbols": cleaned})


def add_symbols(watchlist_id: uuid.UUID, symbols: Sequence[str]) -> list[str]:
    """Append symbols to a watchlist, skipping any already present.

    Returns the symbols actually added (in order). Raises ``ValueError`` for an
    unknown id.
    """
    additions = normalize_symbols(symbols)
    now = datetime.now(UTC)
    added: list[str] = []
    with session_scope() as s:
        target = s.get(Watchlist, watchlist_id)
        if target is None:
            raise ValueError(f"no watchlist with id {watchlist_id}")
        current = list(target.symbols)
        present = set(current)
        for sym in additions:
            if sym not in present:
                current.append(sym)
                present.add(sym)
                added.append(sym)
        if added:
            target.symbols = current
            target.updated_at = now
    if added:
        _audit("watchlist_symbols_added", {"id": str(watchlist_id), "symbols": added})
    return added


def remove_symbol(watchlist_id: uuid.UUID, symbol: str) -> None:
    """Remove one symbol from a watchlist (no-op if absent). Raises
    ``ValueError`` for an unknown id."""
    sym = symbol.strip().upper()
    now = datetime.now(UTC)
    with session_scope() as s:
        target = s.get(Watchlist, watchlist_id)
        if target is None:
            raise ValueError(f"no watchlist with id {watchlist_id}")
        remaining = [x for x in target.symbols if x != sym]
        if len(remaining) != len(target.symbols):
            target.symbols = remaining
            target.updated_at = now
    _audit("watchlist_symbol_removed", {"id": str(watchlist_id), "symbol": sym})


def activate_watchlist(watchlist_id: uuid.UUID) -> None:
    """Make ``watchlist_id`` the single active watchlist, atomically.

    Deactivates every currently-active row, then activates the target — in one
    transaction. Raises ``ValueError`` if the watchlist does not exist.
    """
    now = datetime.now(UTC)
    with session_scope() as s:
        target = s.get(Watchlist, watchlist_id)
        if target is None:
            raise ValueError(f"no watchlist with id {watchlist_id}")
        s.execute(
            update(Watchlist)
            .where(Watchlist.is_active.is_(True))
            .values(is_active=False, updated_at=now)
        )
        target.is_active = True
        target.updated_at = now
    _audit("watchlist_activated", {"id": str(watchlist_id)})


def delete_watchlist(watchlist_id: uuid.UUID) -> None:
    """Delete a watchlist. Raises ``ValueError`` if it does not exist.

    If the deleted list was the active one and other watchlists remain, the
    newest survivor is activated so the runner keeps a live list.
    """
    now = datetime.now(UTC)
    with session_scope() as s:
        target = s.get(Watchlist, watchlist_id)
        if target is None:
            raise ValueError(f"no watchlist with id {watchlist_id}")
        was_active = target.is_active
        name = target.name
        s.delete(target)
        if was_active:
            s.flush()
            survivor = s.scalars(
                select(Watchlist).order_by(Watchlist.created_at.desc()).limit(1)
            ).first()
            if survivor is not None:
                survivor.is_active = True
                survivor.updated_at = now
    _audit("watchlist_deleted", {"id": str(watchlist_id), "name": name})


def _name_taken(s: Session, name: str, exclude: uuid.UUID | None = None) -> bool:
    """True if another watchlist already uses ``name`` (case-sensitive)."""
    stmt = select(Watchlist.id).where(Watchlist.name == name)
    if exclude is not None:
        stmt = stmt.where(Watchlist.id != exclude)
    return s.scalars(stmt.limit(1)).first() is not None


def _audit(event: str, payload: dict[str, object]) -> None:
    from trident.audit.log import record

    record(event, actor="watchlist_store", payload=payload)


def _row_to_record(row: Watchlist) -> WatchlistRecord:
    return WatchlistRecord(
        id=row.id,
        name=row.name,
        symbols=list(row.symbols),
        source=row.source,
        is_active=row.is_active,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
