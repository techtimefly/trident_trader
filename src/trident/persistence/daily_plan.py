"""Per-day trading plan: the user's capital budget + day-trade cap.

One row per trading day in the ``daily_plans`` table. Mirrors ``state.py`` —
kept in ``persistence/`` so the dashboard and the runners share one source of
truth. The risk gate stays a pure function: these accessors run in the runner,
which feeds the results into the gate as plain dataclass fields.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from trident.clock import ET, nth_business_day_back
from trident.persistence.models import DailyPlan, Order
from trident.persistence.session import session_scope


@dataclass(frozen=True)
class DailyPlanRecord:
    """A day's plan as a plain value object, decoupled from the ORM row."""

    trading_day: date
    budget_pct: Decimal | None  # capital budget, percent of equity; None = no cap
    max_day_trades: int | None  # rolling 5-business-day cap; None = no cap


def get_for_day(day: date) -> DailyPlanRecord | None:
    """The plan for ``day``, or None if the user has not set one."""
    with session_scope() as s:
        row = s.get(DailyPlan, day)
        if row is None:
            return None
        return DailyPlanRecord(
            trading_day=row.trading_day,
            budget_pct=row.budget_pct,
            max_day_trades=row.max_day_trades,
        )


def upsert(
    day: date,
    budget_pct: Decimal | None,
    max_day_trades: int | None,
    actor: str = "dashboard",
) -> None:
    """Create or replace the plan for ``day``. A None field means 'no cap'."""
    now = datetime.now(UTC)
    with session_scope() as s:
        stmt = (
            insert(DailyPlan)
            .values(
                trading_day=day,
                budget_pct=budget_pct,
                max_day_trades=max_day_trades,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_update(
                index_elements=["trading_day"],
                set_={
                    "budget_pct": budget_pct,
                    "max_day_trades": max_day_trades,
                    "updated_at": now,
                },
            )
        )
        s.execute(stmt)
    # Audit it. Local import dodges a circular import via audit.log.
    from trident.audit.log import record

    record(
        "daily_plan_updated",
        actor=actor,
        payload={
            "trading_day": day.isoformat(),
            "budget_pct": str(budget_pct) if budget_pct is not None else None,
            "max_day_trades": max_day_trades,
        },
    )


def _et_day_bounds(first: date, last: date) -> tuple[datetime, datetime]:
    """UTC datetimes spanning the start of ``first`` to the end of ``last`` in ET."""
    start = datetime.combine(first, time.min, tzinfo=ET).astimezone(UTC)
    end = datetime.combine(last, time.max, tzinfo=ET).astimezone(UTC)
    return start, end


def notional_deployed_today(day: date) -> Decimal:
    """Cumulative notional of entries opened on ``day`` (ET calendar day).

    Sums ``avg_fill_price * qty`` over filled ``orders`` rows submitted that day.
    The ``orders`` table holds only parent bracket orders (children inherit the
    parent id and are skipped by the order tracker), so this is exactly
    'entries opened today'. Returns Decimal("0") when nothing has filled.
    """
    start, end = _et_day_bounds(day, day)
    total = Decimal("0")
    with session_scope() as s:
        rows = s.execute(
            select(Order.avg_fill_price, Order.qty).where(
                Order.state == "filled",
                Order.avg_fill_price.is_not(None),
                Order.submitted_at >= start,
                Order.submitted_at <= end,
            )
        ).all()
    for price, qty in rows:
        if price is not None:
            total += price * Decimal(qty)
    return total


def day_trades_in_window(day: date, window: int = 5) -> int:
    """Count day-trades in the rolling ``window``-business-day span ending on ``day``.

    The bot flattens every position at EOD, so each filled parent bracket order
    is one day trade. The window is inclusive of ``day`` and of the boundary
    trading day. Terminal non-filled states (canceled/rejected/expired) never
    became a position and are not counted.
    """
    cutoff = nth_business_day_back(day, window)
    start, end = _et_day_bounds(cutoff, day)
    with session_scope() as s:
        count = s.execute(
            select(func.count(Order.id)).where(
                Order.state == "filled",
                Order.submitted_at >= start,
                Order.submitted_at <= end,
            )
        ).scalar_one()
    return int(count)
