from __future__ import annotations

import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy.orm import Session

from trident.persistence.models import AuditEvent
from trident.persistence.session import session_scope
from trident.settings import get_settings


def configure_logging() -> None:
    settings = get_settings()
    settings.log_dir.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=settings.log_level,
        format="%(message)s",
        stream=sys.stdout,
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(settings.log_level)
        ),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)


def record(
    event_type: str,
    actor: str,
    payload: dict[str, Any],
    session: Session | None = None,
) -> None:
    """Append an event to the audit log. Owns its session if none is provided."""

    ts = datetime.now(UTC)
    log = get_logger("audit")
    log.info(event_type, actor=actor, **payload)

    event = AuditEvent(ts=ts, event_type=event_type, actor=actor, payload=payload)
    if session is not None:
        session.add(event)
        return

    with session_scope() as s:
        s.add(event)


def write_jsonl(path: Path, event_type: str, actor: str, payload: dict[str, Any]) -> None:
    """Side-channel write to a JSONL file. Useful when the DB is unavailable."""

    path.parent.mkdir(parents=True, exist_ok=True)
    import json

    line = json.dumps(
        {
            "ts": datetime.now(UTC).isoformat(),
            "event_type": event_type,
            "actor": actor,
            "payload": payload,
        },
        default=str,
    )
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
