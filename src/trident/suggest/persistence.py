"""Persist an AI suggestion run + its suggestions so the dashboard can show them.

Mirrors ``trident.screener.persistence``: one ``SuggestionRun`` row plus one
``SuggestionRow`` per suggested symbol, all in a single ``session_scope``
transaction, with an audit event for the trail.

A degraded run (no API key, nothing to review, an API error) is still saved —
``ok`` is False and ``notice`` carries the explanation — so the dashboard panel
can show an honest "why there are no suggestions" state.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select

from trident.persistence.models import SuggestionRow, SuggestionRun
from trident.persistence.session import session_scope
from trident.suggest.suggestion import StockSuggestion, SuggestionResult


def save_suggestions(
    *,
    result: SuggestionResult,
    screen_run_id: uuid.UUID | None = None,
    actor: str = "suggest_cli",
) -> uuid.UUID:
    """Insert one SuggestionRun + one SuggestionRow per suggestion. Returns the
    run id.

    ``screen_run_id`` links the run to the screen it reviewed (None when no
    screen was available). A not-ok ``result`` is persisted too — with ``ok``
    False, the model id and notice recorded, and no child rows.
    """
    run_id = uuid.uuid4()
    with session_scope() as s:
        s.add(
            SuggestionRun(
                id=run_id,
                started_at=datetime.now(UTC),
                ok=result.ok,
                model=result.model,
                notice=result.notice,
                screen_run_id=screen_run_id,
            )
        )
        for suggestion in result.suggestions:
            s.add(
                SuggestionRow(
                    id=uuid.uuid4(),
                    run_id=run_id,
                    rank=suggestion.rank,
                    symbol=suggestion.symbol,
                    rationale=suggestion.rationale,
                    confidence=suggestion.confidence,
                )
            )
    # Audit it. Local import dodges a circular import via audit.log.
    from trident.audit.log import record

    record(
        "suggestion_run_saved",
        actor=actor,
        payload={
            "run_id": str(run_id),
            "ok": result.ok,
            "model": result.model,
            "count": result.count,
        },
    )
    return run_id


@dataclass(frozen=True)
class LatestSuggestions:
    """The most recent suggestion run as a plain value object for the dashboard."""

    run_id: uuid.UUID
    started_at: datetime
    ok: bool
    model: str
    notice: str
    suggestions: tuple[StockSuggestion, ...]


def get_latest_suggestions() -> LatestSuggestions | None:
    """Read the most recent suggestion run and its rows, or None.

    Returns None when no suggestion run has ever happened. Propagates DB errors
    so the caller (the dashboard panel) can degrade to a placeholder.
    """
    with session_scope() as s:
        run = s.scalars(
            select(SuggestionRun).order_by(SuggestionRun.started_at.desc()).limit(1)
        ).first()
        if run is None:
            return None
        rows = list(
            s.scalars(
                select(SuggestionRow)
                .where(SuggestionRow.run_id == run.id)
                .order_by(SuggestionRow.rank)
            )
        )
        return LatestSuggestions(
            run_id=run.id,
            started_at=run.started_at,
            ok=run.ok,
            model=run.model,
            notice=run.notice,
            suggestions=tuple(
                StockSuggestion(
                    symbol=r.symbol,
                    rationale=r.rationale,
                    confidence=r.confidence,
                    rank=r.rank,
                )
                for r in rows
            ),
        )
