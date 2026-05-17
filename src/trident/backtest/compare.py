"""Run several strategies over the same days for an honest side-by-side compare.

Fairness is the whole point: every strategy sees the *same* bars, the *same*
cost model, and the *same* sizing equity, so the resulting trade lists differ
only because the strategies differ. Bar fetching (network I/O) is left to the
caller — this stays a pure function over pre-fetched bars, exactly like
:func:`trident.backtest.engine.run_day`.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from trident.backtest.costs import ZERO_COST, CostModel
from trident.backtest.engine import run_day
from trident.backtest.simulator import SimulatedTrade
from trident.data.bars import Bar
from trident.risk.limits import RiskLimits


def compare_strategies(
    strategy_names: list[str],
    bars_by_day: dict[date, list[Bar]],
    equity: Decimal,
    limits: RiskLimits,
    watchlist: list[str],
    costs: CostModel = ZERO_COST,
    log: Any | None = None,
) -> dict[str, list[SimulatedTrade]]:
    """Replay each strategy over the same days; return per-strategy trade lists.

    ``bars_by_day`` maps a trading day to that day's 1-min bars across
    ``watchlist``. The caller fetches each day's bars once and shares them
    across every strategy — that shared input is what makes the comparison
    fair. Days are replayed in calendar order; the result dict preserves the
    ``strategy_names`` order. An unknown strategy name raises ``ValueError``
    (via the registry) rather than being silently skipped.
    """
    results: dict[str, list[SimulatedTrade]] = {}
    for name in strategy_names:
        trades: list[SimulatedTrade] = []
        for d, bars in sorted(bars_by_day.items()):
            trades.extend(
                run_day(d, bars, equity, limits, watchlist, costs, log, strategy_name=name)
            )
        results[name] = trades
    return results
