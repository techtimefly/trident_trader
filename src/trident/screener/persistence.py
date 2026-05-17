"""Persist a screen run + its matched rows so the dashboard can show them.

Mirrors ``trident.backtest.persistence.save_replay``: one ``ScreenRun`` row plus
one ``ScreenResultRow`` per matched symbol, all in a single ``session_scope``
transaction, with an audit event for the trail.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select

from trident.persistence.models import ScreenResultRow, ScreenRun
from trident.persistence.session import session_scope
from trident.screener.criteria import ScreenCandidate, ScreenCriteria, ScreenResult


def save_screen(
    *,
    result: ScreenResult,
    universe_size: int,
    lookback_days: int,
    actor: str = "screen_cli",
) -> uuid.UUID:
    """Insert one ScreenRun + one ScreenResultRow per match. Returns the run id.

    ``universe_size`` is the number of symbols requested from Alpaca (which can
    exceed ``result.scanned`` — symbols with no bar data are dropped before the
    pure engine ever sees them).
    """
    crit = result.criteria
    run_id = uuid.uuid4()
    with session_scope() as s:
        s.add(
            ScreenRun(
                id=run_id,
                started_at=datetime.now(UTC),
                universe_size=universe_size,
                scanned=result.scanned,
                matched=result.matched,
                lookback_days=lookback_days,
                min_price=crit.min_price,
                max_price=crit.max_price,
                min_avg_volume=crit.min_avg_volume,
                min_change_pct=crit.min_change_pct,
                max_change_pct=crit.max_change_pct,
            )
        )
        for idx, cand in enumerate(result.matches, start=1):
            s.add(
                ScreenResultRow(
                    id=uuid.uuid4(),
                    run_id=run_id,
                    rank=idx,
                    symbol=cand.symbol,
                    price=cand.price,
                    avg_volume=cand.avg_volume,
                    change_pct=cand.change_pct,
                )
            )
    # Audit it. Local import dodges a circular import via audit.log.
    from trident.audit.log import record

    record(
        "screen_run_saved",
        actor=actor,
        payload={
            "run_id": str(run_id),
            "universe_size": universe_size,
            "scanned": result.scanned,
            "matched": result.matched,
        },
    )
    return run_id


@dataclass(frozen=True)
class LatestScreen:
    """The most recent screen run as a plain value object for the dashboard."""

    run_id: uuid.UUID
    started_at: datetime
    universe_size: int
    scanned: int
    matched: int
    lookback_days: int
    criteria: ScreenCriteria
    matches: tuple[ScreenCandidate, ...]


def get_latest_screen() -> LatestScreen | None:
    """Read the most recent screen run and its ranked matches, or None.

    Returns None when no screen has ever run. Propagates DB errors so the
    caller (the dashboard panel) can degrade to a placeholder.
    """
    with session_scope() as s:
        run = s.scalars(
            select(ScreenRun).order_by(ScreenRun.started_at.desc()).limit(1)
        ).first()
        if run is None:
            return None
        rows = list(
            s.scalars(
                select(ScreenResultRow)
                .where(ScreenResultRow.run_id == run.id)
                .order_by(ScreenResultRow.rank)
            )
        )
        return LatestScreen(
            run_id=run.id,
            started_at=run.started_at,
            universe_size=run.universe_size,
            scanned=run.scanned,
            matched=run.matched,
            lookback_days=run.lookback_days,
            criteria=ScreenCriteria(
                min_price=run.min_price,
                max_price=run.max_price,
                min_avg_volume=run.min_avg_volume,
                min_change_pct=run.min_change_pct,
                max_change_pct=run.max_change_pct,
            ),
            matches=tuple(
                ScreenCandidate(
                    symbol=r.symbol,
                    price=r.price,
                    avg_volume=r.avg_volume,
                    change_pct=r.change_pct,
                )
                for r in rows
            ),
        )
