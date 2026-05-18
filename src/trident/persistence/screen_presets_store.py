"""DB read/write helpers for the screen_presets table — managed screener config.

``criteria_to_json`` / ``criteria_from_json`` are pure converters between a
:class:`~trident.screener.criteria.ScreenCriteria` and a JSON-safe dict; the
rest touch the DB via ``session_scope``. Mirrors ``watchlist_store`` — a screen
preset is to the screener what an active watchlist row is to the runner.

Exactly one preset is active at a time. :func:`get_active_preset` is what
``trident.screener.presets.resolve_screen_criteria`` reads.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from trident.persistence.models import ScreenPreset
from trident.persistence.session import session_scope
from trident.screener.criteria import ScreenCriteria

VALID_SOURCES: frozenset[str] = frozenset({"static", "manual"})


@dataclass(frozen=True)
class ScreenPresetRecord:
    """A screen preset as a plain value object, decoupled from the ORM."""

    id: uuid.UUID
    name: str
    criteria: ScreenCriteria
    lookback_days: int
    source: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


def criteria_to_json(c: ScreenCriteria) -> dict[str, Any]:
    """Serialize a :class:`ScreenCriteria` to a JSON-safe dict. Pure.

    Decimal bounds become strings (JSON has no Decimal type); the sector /
    exchange tuples become lists; ``None`` bounds are kept as ``null`` so the
    round-trip through :func:`criteria_from_json` is exact.
    """
    return {
        "min_price": None if c.min_price is None else str(c.min_price),
        "max_price": None if c.max_price is None else str(c.max_price),
        "min_avg_volume": c.min_avg_volume,
        "min_change_pct": None if c.min_change_pct is None else str(c.min_change_pct),
        "max_change_pct": None if c.max_change_pct is None else str(c.max_change_pct),
        "min_market_cap": c.min_market_cap,
        "max_market_cap": c.max_market_cap,
        "sectors": list(c.sectors),
        "exchanges": list(c.exchanges),
    }


def criteria_from_json(d: dict[str, Any]) -> ScreenCriteria:
    """Rebuild a :class:`ScreenCriteria` from a :func:`criteria_to_json` dict.

    Pure. Tolerant of missing keys (treated as 'no bound') so a preset
    serialized before the criteria set grew still loads.
    """

    def _dec(key: str) -> Decimal | None:
        v = d.get(key)
        return None if v is None else Decimal(str(v))

    def _int(key: str) -> int | None:
        v = d.get(key)
        return None if v is None else int(v)

    return ScreenCriteria(
        min_price=_dec("min_price"),
        max_price=_dec("max_price"),
        min_avg_volume=_int("min_avg_volume"),
        min_change_pct=_dec("min_change_pct"),
        max_change_pct=_dec("max_change_pct"),
        min_market_cap=_int("min_market_cap"),
        max_market_cap=_int("max_market_cap"),
        sectors=tuple(d.get("sectors") or ()),
        exchanges=tuple(d.get("exchanges") or ()),
    )


def list_presets() -> list[ScreenPresetRecord]:
    """All presets, newest-first (for the dashboard list)."""
    with session_scope() as s:
        rows = list(s.scalars(select(ScreenPreset).order_by(ScreenPreset.created_at.desc())))
        return [_row_to_record(r) for r in rows]


def get_active_preset() -> ScreenPresetRecord | None:
    """The most-recently-activated preset, or None if none is active."""
    with session_scope() as s:
        row = s.scalars(
            select(ScreenPreset)
            .where(ScreenPreset.is_active.is_(True))
            .order_by(ScreenPreset.created_at.desc())
            .limit(1)
        ).first()
        if row is None:
            return None
        return _row_to_record(row)


def upsert_preset(
    name: str,
    criteria: ScreenCriteria,
    lookback_days: int,
    source: str = "manual",
) -> uuid.UUID:
    """Create a preset, or update the existing one with the same name.

    Does not change which preset is active — use :func:`activate_preset` for
    that. Returns the preset's UUID (the existing one on an update).

    Raises ``ValueError`` for a blank name, an unknown source, or
    ``lookback_days`` below 2.
    """
    clean_name = name.strip()
    if not clean_name:
        raise ValueError("preset name must not be blank")
    if source not in VALID_SOURCES:
        raise ValueError(
            f"Invalid source {source!r}; must be one of {sorted(VALID_SOURCES)}"
        )
    if lookback_days < 2:
        raise ValueError("lookback_days must be at least 2")

    now = datetime.now(UTC)
    new_id = uuid.uuid4()
    payload = criteria_to_json(criteria)
    with session_scope() as s:
        stmt = (
            insert(ScreenPreset)
            .values(
                id=new_id,
                name=clean_name,
                criteria=payload,
                lookback_days=lookback_days,
                source=source,
                is_active=False,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_update(
                index_elements=["name"],
                set_={
                    "criteria": payload,
                    "lookback_days": lookback_days,
                    "source": source,
                    "updated_at": now,
                },
            )
            .returning(ScreenPreset.id)
        )
        result_id: uuid.UUID = s.execute(stmt).scalar_one()
    from trident.audit.log import record

    record(
        "screen_preset_saved",
        actor="screen_presets_store",
        payload={"id": str(result_id), "name": clean_name, "source": source},
    )
    return result_id


def activate_preset(preset_id: uuid.UUID) -> None:
    """Make ``preset_id`` the single active preset, atomically.

    Deactivates every currently-active row, then activates the target — in one
    transaction. Raises ``ValueError`` if the preset does not exist.
    """
    now = datetime.now(UTC)
    with session_scope() as s:
        target = s.get(ScreenPreset, preset_id)
        if target is None:
            raise ValueError(f"no screen preset with id {preset_id}")
        s.execute(
            update(ScreenPreset)
            .where(ScreenPreset.is_active.is_(True))
            .values(is_active=False, updated_at=now)
        )
        target.is_active = True
        target.updated_at = now
    from trident.audit.log import record

    record(
        "screen_preset_activated",
        actor="screen_presets_store",
        payload={"id": str(preset_id)},
    )


def delete_preset(preset_id: uuid.UUID) -> None:
    """Delete a preset. Raises ``ValueError`` if it does not exist."""
    with session_scope() as s:
        target = s.get(ScreenPreset, preset_id)
        if target is None:
            raise ValueError(f"no screen preset with id {preset_id}")
        name = target.name
        s.delete(target)
    from trident.audit.log import record

    record(
        "screen_preset_deleted",
        actor="screen_presets_store",
        payload={"id": str(preset_id), "name": name},
    )


def _row_to_record(row: ScreenPreset) -> ScreenPresetRecord:
    return ScreenPresetRecord(
        id=row.id,
        name=row.name,
        criteria=criteria_from_json(dict(row.criteria)),
        lookback_days=row.lookback_days,
        source=row.source,
        is_active=row.is_active,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
