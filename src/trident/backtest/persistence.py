"""Persist a replay run + its trades so the dashboard can show them."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from trident.backtest.simulator import SimulatedTrade
from trident.backtest.stats import summarize
from trident.persistence.models import ReplayRun, ReplayTrade
from trident.persistence.session import session_scope


def save_replay(
    *,
    days: list[datetime],
    equity: Decimal,
    watchlist: list[str],
    strategy: str,
    trades: list[SimulatedTrade],
) -> uuid.UUID:
    """Insert one ReplayRun + one ReplayTrade per simulated trade. Returns run_id."""
    if not days:
        raise ValueError("save_replay requires at least one day")

    summary = summarize(trades)
    run_id = uuid.uuid4()
    with session_scope() as s:
        s.add(
            ReplayRun(
                id=run_id,
                started_at=datetime.now(UTC),
                first_day=_to_dt(days[0]),
                last_day=_to_dt(days[-1]),
                days=len(days),
                equity=equity,
                watchlist={"symbols": watchlist},
                strategy=strategy,
                num_trades=summary.num_trades,
                wins=summary.wins,
                losses=summary.losses,
                total_pnl=summary.total_pnl,
                avg_r=summary.avg_r,
            )
        )
        for t in trades:
            s.add(
                ReplayTrade(
                    id=uuid.uuid4(),
                    run_id=run_id,
                    trade_date=_floor_to_date(t.signal.ts),
                    symbol=t.signal.symbol,
                    side=t.signal.side,
                    qty=t.qty,
                    entry_ts=t.signal.ts,
                    entry_price=t.entry_price,
                    stop_price=t.signal.stop_price,
                    target_price=t.signal.target_price,
                    exit_ts=_parse_iso(t.exit_ts_iso),
                    exit_reason=t.exit_reason,
                    exit_price=t.exit_price,
                    pnl=t.pnl,
                    r_multiple=t.r_multiple,
                )
            )
    return run_id


def _to_dt(d: datetime | object) -> datetime:
    if isinstance(d, datetime):
        return d
    # date object — promote to midnight UTC.
    return datetime(d.year, d.month, d.day, tzinfo=UTC)  # type: ignore[attr-defined]


def _floor_to_date(ts: datetime) -> datetime:
    return datetime(ts.year, ts.month, ts.day, tzinfo=ts.tzinfo or UTC)


def _parse_iso(s: str) -> datetime:
    s = s.replace("Z", "+00:00") if s.endswith("Z") else s
    return datetime.fromisoformat(s)
