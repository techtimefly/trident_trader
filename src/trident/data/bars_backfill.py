"""Rebuild a BarStore from bars already persisted to Postgres.

On a mid-session restart the in-memory :class:`BarStore` and every strategy's
per-day state are lost. The runner persists every bar it sees to the ``bars``
table, so on startup it can reload the day's bars and replay them — both to
warm the store and to rebuild strategy state (the opening range, VWAP
accumulators, the entered flags).

``fill_store`` is pure and unit-tested; the DB query is verified by smoke.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, time

from sqlalchemy import select

from trident.clock import ET
from trident.data.bars import Bar, BarStore
from trident.persistence.models import Bar as BarRow
from trident.persistence.session import session_scope
from trident.strategies.base import Strategy


def load_day_bars(
    symbols: list[str], day: date, timeframe: str = "1min"
) -> list[Bar]:
    """Every persisted ``timeframe`` bar for ``symbols`` on the ET calendar
    ``day``, ascending by timestamp."""
    if not symbols:
        return []
    start = datetime.combine(day, time.min, tzinfo=ET).astimezone(UTC)
    end = datetime.combine(day, time.max, tzinfo=ET).astimezone(UTC)
    with session_scope() as s:
        rows = s.scalars(
            select(BarRow)
            .where(
                BarRow.symbol.in_(symbols),
                BarRow.timeframe == timeframe,
                BarRow.ts >= start,
                BarRow.ts <= end,
            )
            .order_by(BarRow.ts)
        ).all()
        return [
            Bar(
                symbol=r.symbol,
                ts=r.ts,
                timeframe=r.timeframe,
                open=r.open,
                high=r.high,
                low=r.low,
                close=r.close,
                volume=r.volume,
            )
            for r in rows
        ]


def fill_store(store: BarStore, bars: list[Bar]) -> int:
    """Append ``bars`` (ascending by ts) into ``store``. Returns the count fed.

    The store itself drops a bar that is not newer than the last for its
    (symbol, timeframe), so a re-backfill is idempotent.
    """
    for bar in bars:
        store.append(bar)
    return len(bars)


def backfill_store(
    store: BarStore, symbols: list[str], day: date, timeframe: str = "1min"
) -> int:
    """Load ``day``'s persisted bars for ``symbols`` into ``store``. Returns the
    number of bars loaded."""
    return fill_store(store, load_day_bars(symbols, day, timeframe))


def replay_bars_through(strategy: Strategy, store: BarStore, bars: list[Bar]) -> int:
    """Append ``bars`` to ``store`` and feed each through ``strategy.on_bar`` to
    rebuild the strategy's in-memory state (opening range, VWAP accumulators,
    the entered flags). Any signal returned during replay is discarded — only
    state is rebuilt, no orders are placed. Returns the count replayed."""
    for bar in bars:
        store.append(bar)
        strategy.on_bar(bar, store)
    return len(bars)


def recover_strategy_state(
    strategy: Strategy,
    store: BarStore,
    symbols: list[str],
    day: date,
    timeframe: str = "1min",
) -> int:
    """Mid-session crash recovery: reload ``day``'s persisted bars and replay
    them through ``strategy``, so a restart resumes with the store warm and the
    strategy's state reconstructed. Returns the number of bars replayed."""
    return replay_bars_through(strategy, store, load_day_bars(symbols, day, timeframe))
