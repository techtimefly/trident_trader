"""Walk-forward evaluation: the same strategy across consecutive day windows.

This is **not** a parameter optimizer — the ORB strategy has no fitted
parameters, and tuning against history is the curve-fitting CLAUDE.md forbids.
What this does is partition a backtest's trading days into consecutive windows
and summarize each, so you can see whether performance holds across time
(regime sensitivity) instead of trusting a single blended number.

Windows are cut over the *calendar* trading days that were replayed — passed in
as ``trading_days`` — not only the days that happened to produce a trade. A
window with zero trades is kept and reported as such; that is itself a signal.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from trident.backtest.simulator import SimulatedTrade
from trident.backtest.stats import Summary, summarize
from trident.clock import ET


@dataclass(frozen=True)
class WalkForwardWindow:
    index: int  # 0-based
    first_day: date
    last_day: date
    num_days: int  # trading days in this window (<= window_days; final may be short)
    summary: Summary


def walk_forward(
    trades: Sequence[SimulatedTrade],
    trading_days: Sequence[date],
    window_days: int,
) -> list[WalkForwardWindow]:
    """Chunk ``trading_days`` into consecutive windows of ``window_days`` days
    and summarize the trades that fall in each.

    Trades are placed by their signal date (ET). A short final window is kept
    as-is. Returns ``[]`` when no days were replayed. Raises ``ValueError`` if
    ``window_days < 1``.
    """
    if window_days < 1:
        raise ValueError("window_days must be >= 1")

    days = sorted(trading_days)
    if not days:
        return []

    by_day: dict[date, list[SimulatedTrade]] = {}
    for t in trades:
        d = t.signal.ts.astimezone(ET).date()
        by_day.setdefault(d, []).append(t)

    windows: list[WalkForwardWindow] = []
    for start in range(0, len(days), window_days):
        chunk = days[start : start + window_days]
        chunk_trades: list[SimulatedTrade] = []
        for d in chunk:
            chunk_trades.extend(by_day.get(d, []))
        windows.append(
            WalkForwardWindow(
                index=len(windows),
                first_day=chunk[0],
                last_day=chunk[-1],
                num_days=len(chunk),
                summary=summarize(chunk_trades),
            )
        )
    return windows
