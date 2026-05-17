"""The strategy + risk-gate + fill-simulation loop for one trading day.

Shared by the idealistic replay (``scripts/replay.py``) and the honest backtest
(``scripts/backtest.py``); the two differ only in the :class:`CostModel` passed
in. Bar fetching is deliberately left to the caller (network I/O at the script
edge) so this module stays pure and testable.
"""
from __future__ import annotations

from datetime import date, time
from decimal import Decimal
from typing import Any

from trident.backtest.costs import ZERO_COST, CostModel
from trident.backtest.simulator import SimulatedTrade, simulate_trade
from trident.clock import ET
from trident.data.bars import Bar, BarStore
from trident.risk.gate import AccountState, MarketState, evaluate
from trident.risk.limits import RiskLimits
from trident.strategies.registry import build_strategy


def run_day(
    d: date,
    bars: list[Bar],
    equity: Decimal,
    limits: RiskLimits,
    watchlist: list[str],
    costs: CostModel = ZERO_COST,
    log: Any | None = None,
    strategy_name: str = "orb_5m",
) -> list[SimulatedTrade]:
    """Replay one day's bars through the strategy + gate and simulate fills.

    ``bars`` must be the day's 1-min bars across ``watchlist``, ascending by ts.
    With ``costs=ZERO_COST`` the fills are idealistic (replay behaviour); pass a
    non-zero :class:`CostModel` for an honest backtest. ``strategy_name`` selects
    the strategy from the registry (default ORB). Pure apart from the optional
    structured ``log``.
    """
    if not bars:
        if log is not None:
            log.warning("no_bars_for_day", date=d.isoformat())
        return []

    strategy = build_strategy(strategy_name, watchlist)
    store = BarStore()
    trades: list[SimulatedTrade] = []

    for bar in bars:
        store.append(bar)
        sig = strategy.on_bar(bar, store)
        if sig is None:
            continue

        bar_et = bar.ts.astimezone(ET)
        # Every signal is gated as if no positions are currently open: the
        # strategy enforces one entry per symbol per day, and the day is replayed
        # bar-by-bar without tracking real-time fills, so the gate's
        # max_concurrent_positions check is intentionally not exercised here.
        account = AccountState(
            equity=equity,
            starting_equity_today=equity,
            buying_power=equity * Decimal("2"),
            open_positions={},
        )
        market = MarketState()
        decision = evaluate(sig, account, market, limits, time(bar_et.hour, bar_et.minute))
        if not decision.approved:
            if log is not None:
                log.info(
                    "signal_rejected", date=d.isoformat(), symbol=sig.symbol, reason=decision.reason
                )
            continue

        followups = [b for b in bars if b.symbol == sig.symbol and b.ts > sig.ts]
        trade = simulate_trade(sig, decision.shares, followups, costs)
        if trade is not None:
            trades.append(trade)
            if log is not None:
                log.info(
                    "simulated_trade",
                    date=d.isoformat(),
                    symbol=sig.symbol,
                    qty=trade.qty,
                    entry=str(trade.entry_price),
                    exit_reason=trade.exit_reason,
                    exit_price=str(trade.exit_price),
                    pnl=str(trade.pnl),
                    r=f"{trade.r_multiple:.2f}",
                )
    return trades
