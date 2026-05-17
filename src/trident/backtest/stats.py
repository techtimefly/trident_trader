"""Aggregate statistics over a set of simulated trades.

Pure functions, shared by the replay report, the replay persistence layer, and
the walk-forward harness — so the headline numbers are computed in exactly one
place rather than re-derived inline at each call site.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from trident.backtest.simulator import SimulatedTrade


@dataclass(frozen=True)
class Summary:
    num_trades: int
    wins: int
    losses: int
    gross_pnl: Decimal  # P&L from fill prices, before fees
    total_pnl: Decimal  # net of fees — the headline number
    total_fees: Decimal
    win_rate: Decimal  # percent, 0..100
    avg_r: Decimal
    by_exit: dict[str, int]  # exit_reason -> count


def summarize(trades: Sequence[SimulatedTrade]) -> Summary:
    """Aggregate a flat list of trades into a :class:`Summary`.

    Wins/losses are counted on *net* P&L. ``avg_r`` is the mean of per-trade
    ``r_multiple`` (gross, fee-free). An empty list yields an all-zero Summary.
    """
    n = len(trades)
    wins = sum(1 for t in trades if t.pnl > 0)
    losses = sum(1 for t in trades if t.pnl < 0)
    gross = sum((t.gross_pnl for t in trades), Decimal("0"))
    net = sum((t.pnl for t in trades), Decimal("0"))
    fees = sum((t.entry_fee + t.exit_fee for t in trades), Decimal("0"))
    win_rate = (Decimal(wins) / Decimal(n) * Decimal("100")) if n else Decimal("0")
    avg_r = (
        sum((t.r_multiple for t in trades), Decimal("0")) / Decimal(n) if n else Decimal("0")
    )
    return Summary(
        num_trades=n,
        wins=wins,
        losses=losses,
        gross_pnl=gross,
        total_pnl=net,
        total_fees=fees,
        win_rate=win_rate,
        avg_r=avg_r,
        by_exit=dict(Counter(t.exit_reason for t in trades)),
    )
