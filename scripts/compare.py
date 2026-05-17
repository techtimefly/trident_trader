"""Compare several strategies over the same historical days, side by side.

Every strategy is replayed against the *same* bars with the *same* cost model
and sizing equity, so the only thing that differs is the strategy itself — an
honest comparison. With one strategy registered this is a one-row report; it
earns its keep the moment a second strategy lands.

Costs match scripts/backtest.py (slippage + fees from settings), so the numbers
here are honest, not idealistic. Each strategy's run is persisted separately so
the dashboard can show them independently.

Examples:
    PYTHONPATH=src python scripts/compare.py --days 30
    PYTHONPATH=src python scripts/compare.py --days 60 --strategy orb_5m
    PYTHONPATH=src python scripts/compare.py --date 2026-05-12 --no-persist
"""
from __future__ import annotations

import argparse
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

from replay import fetch_minute_bars, fmt_money, trading_days_back

from trident.audit.log import configure_logging, get_logger
from trident.backtest.compare import compare_strategies
from trident.backtest.costs import CostModel
from trident.backtest.persistence import save_replay
from trident.backtest.simulator import SimulatedTrade
from trident.backtest.stats import summarize
from trident.clock import ET, is_trading_day
from trident.data.bars import Bar
from trident.risk.limits import RiskLimits
from trident.settings import get_settings
from trident.strategies.registry import available_strategies
from trident.watchlist import WATCHLIST

_RULE = "=" * 92
_THIN = "-" * 92


def print_comparison(
    results: dict[str, list[SimulatedTrade]], equity: Decimal, costs: CostModel
) -> None:
    print("\n" + _RULE)
    print("STRATEGY COMPARISON — same days, same cost model, same sizing equity")
    print(
        f"  cost model: slippage={costs.slippage_bps} bps  fee/share={costs.fee_per_share}"
        f"   sizing equity: {fmt_money(equity)}"
    )
    print(_RULE)
    print(
        f"{'Strategy':<16}{'Trades':>8}{'Wins':>7}{'Losses':>8}{'Win%':>8}"
        f"{'Net P&L':>15}{'avg R':>9}"
    )
    print(_THIN)
    for name, trades in results.items():
        s = summarize(trades)
        win_pct = f"{s.win_rate:.0f}%" if s.num_trades else "-"
        avg_r = f"{s.avg_r:+.2f}" if s.num_trades else "-"
        print(
            f"{name:<16}{s.num_trades:>8}{s.wins:>7}{s.losses:>8}{win_pct:>8}"
            f"{fmt_money(s.total_pnl):>15}{avg_r:>9}"
        )
    print(_RULE)
    print()


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(
        description="Compare strategies over the same historical days, side by side."
    )
    parser.add_argument(
        "--date",
        type=date.fromisoformat,
        help="Single trading day, YYYY-MM-DD. Defaults to a window ending on the last trading day.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Compare over the last N trading days (ignored if --date is given). Default: 30.",
    )
    parser.add_argument(
        "--equity",
        type=Decimal,
        default=Decimal("100000"),
        help="Account equity to size positions against. Default: 100000.",
    )
    parser.add_argument(
        "--strategy",
        nargs="+",
        choices=available_strategies(),
        default=available_strategies(),
        help="Strategies to compare. Default: every registered strategy.",
    )
    parser.add_argument(
        "--no-persist",
        action="store_true",
        help="Skip writing the runs to the database (the dashboard won't see them).",
    )
    args = parser.parse_args()

    configure_logging()
    log = get_logger("compare")
    if not settings.alpaca_api_key:
        log.error("missing_alpaca_credentials")
        return 1

    costs = CostModel(
        slippage_bps=settings.backtest_slippage_bps,
        fee_per_share=settings.backtest_fee_per_share,
        min_fee=settings.backtest_min_fee,
        sec_fee_rate=settings.backtest_sec_fee_rate,
        taf_per_share=settings.backtest_taf_per_share,
    )
    limits = RiskLimits(
        risk_per_trade_pct=settings.risk_per_trade_pct,
        daily_loss_limit_pct=settings.daily_loss_limit_pct,
        max_concurrent_positions=settings.max_concurrent_positions,
    )

    if args.date:
        if not is_trading_day(args.date):
            log.error("not_a_trading_day", date=args.date.isoformat())
            return 1
        days = [args.date]
    else:
        anchor = datetime.now(ET).date() - timedelta(days=1)
        while not is_trading_day(anchor):
            anchor = anchor - timedelta(days=1)
        days = trading_days_back(anchor, args.days)

    log.info(
        "comparison_starting",
        strategies=args.strategy,
        days=[d.isoformat() for d in days],
    )

    # Fetch each day's bars once and share them across every strategy — this
    # shared input is what makes the comparison fair.
    bars_by_day: dict[date, list[Bar]] = {}
    for d in days:
        start = datetime.combine(d, time(8, 0), tzinfo=ET).astimezone(UTC)
        end = datetime.combine(d, time(20, 0), tzinfo=ET).astimezone(UTC)
        bars_by_day[d] = fetch_minute_bars(start, end, WATCHLIST)

    results = compare_strategies(
        args.strategy, bars_by_day, args.equity, limits, WATCHLIST, costs, log
    )
    print_comparison(results, args.equity, costs)

    if not args.no_persist:
        day_dts = [datetime(d.year, d.month, d.day, tzinfo=UTC) for d in days]
        for name, trades in results.items():
            if not trades:
                continue
            run_id = save_replay(
                days=day_dts,
                equity=args.equity,
                watchlist=WATCHLIST,
                strategy=name,
                trades=trades,
                mode="honest",
                costs=costs,
            )
            log.info(
                "comparison_run_persisted",
                strategy=name,
                run_id=str(run_id),
                trades=len(trades),
            )
            print(f"Saved {name} run {run_id} ({len(trades)} trades).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
