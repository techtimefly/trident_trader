"""Tiny key/value helpers around the `system_state` table.

Used for the kill switch and the shadow-run heartbeat. Kept here rather than in a
runtime/ module so the dashboard and the shadow runner share a single source of truth.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.dialects.postgresql import insert

from trident.persistence.models import SystemState
from trident.persistence.session import session_scope

KILL_SWITCH_KEY = "kill_switch"
HEARTBEAT_KEY = "shadow_heartbeat"


def _set(key: str, value: str) -> None:
    with session_scope() as s:
        stmt = (
            insert(SystemState)
            .values(key=key, value=value, updated_at=datetime.now(UTC))
            .on_conflict_do_update(
                index_elements=["key"],
                set_={"value": value, "updated_at": datetime.now(UTC)},
            )
        )
        s.execute(stmt)


def _get(key: str) -> tuple[str, datetime] | None:
    with session_scope() as s:
        row = s.get(SystemState, key)
        if row is None:
            return None
        return row.value, row.updated_at


def set_kill_switch(engaged: bool, actor: str = "dashboard") -> None:
    _set(KILL_SWITCH_KEY, "1" if engaged else "0")
    # Audit it. Local import dodges a circular import via audit.log → persistence.session.
    from trident.audit.log import record

    record(
        "kill_switch_toggled",
        actor=actor,
        payload={"engaged": engaged},
    )


def kill_switch_engaged() -> bool:
    got = _get(KILL_SWITCH_KEY)
    if got is None:
        return False
    value, _ = got
    return value == "1"


def write_heartbeat() -> None:
    _set(HEARTBEAT_KEY, datetime.now(UTC).isoformat())


def last_heartbeat() -> datetime | None:
    got = _get(HEARTBEAT_KEY)
    if got is None:
        return None
    _, updated_at = got
    return updated_at
