"""Honest backtest: replay historical bars through the strategy + risk gate with
slippage and fees modeled, then report per-window (walk-forward) summaries.

Unlike replay.py — which is idealistic and answers "did the strategy fire?" —
this answers "what would the strategy actually have netted after costs?" Two
caveats remain, stated honestly:

  - Fills are still *simulated*, not real (entry on the breakout bar, exits at
    stop/target/EOD), just now degraded by slippage and charged fees.
  - Slippage is a *synthetic* basis-point model. Alpaca's IEX feed has no
    bid/ask, so there is no spread to sample — tune --slippage-bps to taste.

Walk-forward here is not a parameter optimizer (the ORB strategy has no fitted
parameters); it splits the replayed days into consecutive windows so you can see
whether performance holds across time instead of trusting one blended number.

Examples:
    PYTHONPATH=src python scripts/backtest.py --days 30
    PYTHONPATH=src python scripts/backtest.py --days 60 --window-days 10 --slippage-bps 3
    PYTHONPATH=src python scripts/backtest.py --date 2026-05-12
"""
from __future__ import annotations

import argparse
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

from replay import fetch_minute_bars, fmt_money, trading_days_back

from trident.audit.log import configure_logging, get_logger
from trident.backtest.costs import CostModel
from trident.backtest.engine import run_day
from trident.backtest.persistence import save_replay
from trident.backtest.simulator import SimulatedTrade
from trident.backtest.stats import summarize
from trident.backtest.walk_forward import walk_forward
from trident.clock import ET, is_trading_day
from trident.risk.limits import RiskLimits
from trident.settings import get_settings
from trident.strategies.registry import available_strategies
from trident.watchlist import WATCHLIST

_RULE = "=" * 105
_THIN = "-" * 105


def print_backtest_report(trades: list[SimulatedTrade], costs: CostModel, equity: Decimal) -> None:
    print("\n" + _RULE)
    print("HONEST BACKTEST — slippage + fees modeled (fills simulated, not real)")
    print(
        f"  cost model: slippage={costs.slippage_bps} bps  fee/share={costs.fee_per_share}  "
        f"min fee={costs.min_fee}  SEC rate={costs.sec_fee_rate}  TAF/share={costs.taf_per_share}"
    )
    print(f"  sizing equity: {fmt_money(equity)}")
    print(_RULE)

    if not trades:
        print("\nNo trades simulated over the requested window.\n")
        return

    print(
        f"{'Time (ET)':<18}{'Sym':<5}{'Side':<6}{'Qty':>5}{'Entry':>10}{'Exit':>10}"
        f"{'Why':>8}{'Gross':>13}{'Fees':>10}{'Net':>13}{'R':>7}"
    )
    print(_THIN)
    for t in sorted(trades, key=lambda x: x.signal.ts):
        et_ts = t.signal.ts.astimezone(ET).strftime("%Y-%m-%d %H:%M")
        print(
            f"{et_ts:<18}{t.signal.symbol:<5}{t.signal.side:<6}{t.qty:>5}"
            f"{t.entry_price!s:>10}{t.exit_price!s:>10}{t.exit_reason:>8}"
            f"{fmt_money(t.gross_pnl):>13}{fmt_money(t.entry_fee + t.exit_fee):>10}"
            f"{fmt_money(t.pnl):>13}{t.r_multiple:>7.2f}"
        )
    print(_THIN)

    s = summarize(trades)
    print(
        f"Trades: {s.num_trades}   wins {s.wins}   losses {s.losses}   "
        f"win rate {s.win_rate:.1f}%"
    )
    print("Exits: " + "  ".join(f"{k}={v}" for k, v in sorted(s.by_exit.items())))
    print(
        f"Gross P&L: {fmt_money(s.gross_pnl)}   Fees: {fmt_money(s.total_fees)}   "
        f"Net P&L: {fmt_money(s.total_pnl)}   avg R {s.avg_r:+.2f}"
    )
    print(_RULE)
    print()


def print_walk_forward(trades: list[SimulatedTrade], days: list[date], window_days: int) -> None:
    windows = walk_forward(trades, days, window_days)
    if not windows:
        return

    print(f"Walk-forward — consecutive {window_days}-trading-day windows")
    print(_THIN)
    print(
        f"{'Window':<8}{'Period':<28}{'Days':>5}{'Trades':>8}{'Win%':>8}"
        f"{'Net P&L':>15}{'avg R':>9}"
    )
    print(_THIN)
    for w in windows:
        s = w.summary
        period = f"{w.first_day.isoformat()} - {w.last_day.isoformat()}"
        win_pct = f"{s.win_rate:.0f}%" if s.num_trades else "-"
        avg_r = f"{s.avg_r:+.2f}" if s.num_trades else "-"
        print(
            f"{w.index + 1:<8}{period:<28}{w.num_days:>5}{s.num_trades:>8}{win_pct:>8}"
            f"{fmt_money(s.total_pnl):>15}{avg_r:>9}"
        )
    print(_THIN)
    print()


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(
        description="Honest backtest with slippage + fees and walk-forward windows."
    )
    parser.add_argument(
        "--date",
        type=date.fromisoformat,
        help="Single trading day, YYYY-MM-DD. Defaults to the most recent past trading day.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=1,
        help="Backtest the last N trading days (ignored if --date is given). Default: 1.",
    )
    parser.add_argument(
        "--equity",
        type=Decimal,
        default=Decimal("100000"),
        help="Account equity to size positions against. Default: 100000.",
    )
    parser.add_argument(
        "--slippage-bps",
        type=Decimal,
        default=settings.backtest_slippage_bps,
        help="Synthetic slippage in basis points of the fill price.",
    )
    parser.add_argument(
        "--fee-per-share",
        type=Decimal,
        default=settings.backtest_fee_per_share,
        help="Broker commission per share, per order leg.",
    )
    parser.add_argument(
        "--min-fee",
        type=Decimal,
        default=settings.backtest_min_fee,
        help="Per-order commission floor.",
    )
    parser.add_argument(
        "--sec-fee-rate",
        type=Decimal,
        default=settings.backtest_sec_fee_rate,
        help="SEC fee as a fraction of sell-leg notional.",
    )
    parser.add_argument(
        "--taf-per-share",
        type=Decimal,
        default=settings.backtest_taf_per_share,
        help="FINRA TAF per share, charged on sells.",
    )
    parser.add_argument(
        "--window-days",
        type=int,
        default=5,
        help="Walk-forward window size in trading days. Default: 5.",
    )
    parser.add_argument(
        "--strategy",
        choices=available_strategies(),
        default=settings.default_strategy,
        help="Strategy to backtest. Default: the configured default_strategy.",
    )
    parser.add_argument(
        "--no-persist",
        action="store_true",
        help="Skip writing the run to the database (the dashboard won't see it).",
    )
    args = parser.parse_args()

    configure_logging()
    log = get_logger("backtest")
    if not settings.alpaca_api_key:
        log.error("missing_alpaca_credentials")
        return 1
    if args.window_days < 1:
        log.error("invalid_window_days", window_days=args.window_days)
        return 1

    costs = CostModel(
        slippage_bps=args.slippage_bps,
        fee_per_share=args.fee_per_share,
        min_fee=args.min_fee,
        sec_fee_rate=args.sec_fee_rate,
        taf_per_share=args.taf_per_share,
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
        "backtest_starting",
        days=[d.isoformat() for d in days],
        window_days=args.window_days,
        slippage_bps=str(args.slippage_bps),
    )
    all_trades: list[SimulatedTrade] = []
    for d in days:
        start = datetime.combine(d, time(8, 0), tzinfo=ET).astimezone(UTC)
        end = datetime.combine(d, time(20, 0), tzinfo=ET).astimezone(UTC)
        bars = fetch_minute_bars(start, end, WATCHLIST)
        all_trades.extend(
            run_day(d, bars, args.equity, limits, WATCHLIST, costs, log, strategy_name=args.strategy)
        )

    print_backtest_report(all_trades, costs, args.equity)
    print_walk_forward(all_trades, days, args.window_days)

    if not args.no_persist and all_trades:
        run_id = save_replay(
            days=[datetime(d.year, d.month, d.day, tzinfo=UTC) for d in days],
            equity=args.equity,
            watchlist=WATCHLIST,
            strategy=args.strategy,
            trades=all_trades,
            mode="honest",
            costs=costs,
        )
        log.info("backtest_persisted", run_id=str(run_id), trades=len(all_trades))
        print(f"Saved honest backtest {run_id} ({len(all_trades)} trades) — open the dashboard.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
