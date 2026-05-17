"""Replay historical 1-min bars through the strategy + risk gate, then simulate
fills against the rest of the day. Prints a per-trade report and a summary.

This is NOT a backtest — fills are idealistic (entry = bar close, exits at exact
stop/target with no slippage). Its purpose is to let you sanity-check that the
strategy fires on real data and to see what its trades would have looked like
without waiting for the next market open.

Examples:
    PYTHONPATH=src python scripts/replay.py                       # yesterday
    PYTHONPATH=src python scripts/replay.py --date 2026-05-12
    PYTHONPATH=src python scripts/replay.py --days 10             # last 10 trading days
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from trident.audit.log import configure_logging, get_logger
from trident.backtest.costs import ZERO_COST
from trident.backtest.engine import run_day
from trident.backtest.persistence import save_replay
from trident.backtest.simulator import SimulatedTrade
from trident.backtest.stats import summarize
from trident.clock import ET, is_trading_day
from trident.data.bars import Bar
from trident.risk.limits import RiskLimits
from trident.settings import get_settings
from trident.strategies.registry import available_strategies
from trident.watchlist import WATCHLIST


def fetch_minute_bars(start: datetime, end: datetime, symbols: list[str]) -> list[Bar]:
    settings = get_settings()
    from alpaca.data.enums import DataFeed
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

    client = StockHistoricalDataClient(settings.alpaca_api_key, settings.alpaca_api_secret)
    feed = DataFeed.IEX if settings.alpaca_data_feed.lower() == "iex" else DataFeed.SIP
    req = StockBarsRequest(
        symbol_or_symbols=symbols,
        start=start,
        end=end,
        timeframe=TimeFrame(1, TimeFrameUnit.Minute),
        feed=feed,
    )
    resp = client.get_stock_bars(req)
    out: list[Bar] = []
    for sym in symbols:
        bars = resp.data.get(sym, [])
        for b in bars:
            out.append(
                Bar(
                    symbol=sym,
                    ts=b.timestamp.astimezone(UTC),
                    timeframe="1min",
                    open=Decimal(str(b.open)),
                    high=Decimal(str(b.high)),
                    low=Decimal(str(b.low)),
                    close=Decimal(str(b.close)),
                    volume=int(b.volume),
                )
            )
    out.sort(key=lambda b: b.ts)
    return out


def trading_days_back(end: date, n: int) -> list[date]:
    days: list[date] = []
    d = end
    while len(days) < n:
        if is_trading_day(d):
            days.append(d)
        d = d - timedelta(days=1)
    return list(reversed(days))


def replay_one_day(
    d: date, equity: Decimal, limits: RiskLimits, log: Any, strategy: str = "orb_5m"
) -> list[SimulatedTrade]:
    """Fetch one historical day's 1-min bars and replay them idealistically.

    Idealistic = no slippage, no fees (``ZERO_COST``). For an honest, costed
    backtest use ``scripts/backtest.py``. The strategy + gate + fill loop itself
    lives in ``trident.backtest.engine.run_day``.
    """
    start = datetime.combine(d, time(8, 0), tzinfo=ET).astimezone(UTC)
    end = datetime.combine(d, time(20, 0), tzinfo=ET).astimezone(UTC)
    bars = fetch_minute_bars(start, end, WATCHLIST)
    return run_day(d, bars, equity, limits, WATCHLIST, ZERO_COST, log, strategy_name=strategy)


def fmt_money(d: Decimal) -> str:
    sign = "-" if d < 0 else ""
    return f"{sign}${abs(d):,.2f}"


def print_report(trades: list[SimulatedTrade]) -> None:
    if not trades:
        print("\nNo trades simulated. Either the day was a holiday/weekend, the bars")
        print("did not include a valid opening range, or no breakouts met the volume filter.\n")
        return

    by_symbol_pnl: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))

    print("\n" + "=" * 88)
    print(f"{'Time (ET)':<22}{'Sym':<6}{'Side':<6}{'Qty':>5}{'Entry':>10}{'Exit':>10}{'Why':>8}{'P&L':>14}{'R':>6}")
    print("-" * 88)
    for t in sorted(trades, key=lambda x: x.signal.ts):
        et_ts = t.signal.ts.astimezone(ET).strftime("%Y-%m-%d %H:%M")
        print(
            f"{et_ts:<22}"
            f"{t.signal.symbol:<6}"
            f"{t.signal.side:<6}"
            f"{t.qty:>5}"
            f"{t.entry_price!s:>10}"
            f"{t.exit_price!s:>10}"
            f"{t.exit_reason:>8}"
            f"{fmt_money(t.pnl):>14}"
            f"{t.r_multiple:>6.2f}"
        )
        by_symbol_pnl[t.signal.symbol] += t.pnl
    print("-" * 88)

    s = summarize(trades)
    print(f"Trades: {s.num_trades}   wins {s.wins}   losses {s.losses}   win rate {s.win_rate:.1f}%")
    print("Exits: " + "  ".join(f"{k}={v}" for k, v in sorted(s.by_exit.items())))
    print("By symbol: " + "  ".join(f"{sym}={fmt_money(p)}" for sym, p in sorted(by_symbol_pnl.items())))
    print(f"Total P&L (no fees, no slippage): {fmt_money(s.total_pnl)}   avg R {s.avg_r:+.2f}")
    print("=" * 88)
    print()


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Replay historical bars through the strategy.")
    parser.add_argument(
        "--date",
        type=lambda s: date.fromisoformat(s),
        help="Specific trading day, YYYY-MM-DD. Defaults to the most recent past trading day.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=1,
        help="Replay the last N trading days (ignored if --date is given). Default: 1.",
    )
    parser.add_argument(
        "--equity",
        type=lambda s: Decimal(s),
        default=Decimal("100000"),
        help="Account equity to size positions against. Default: 100000.",
    )
    parser.add_argument(
        "--strategy",
        choices=available_strategies(),
        default=settings.default_strategy,
        help="Strategy to replay. Default: the configured default_strategy.",
    )
    parser.add_argument(
        "--no-persist",
        action="store_true",
        help="Skip writing results to the database (dashboard won't see them).",
    )
    args = parser.parse_args()

    configure_logging()
    log = get_logger("replay")
    if not settings.alpaca_api_key:
        log.error("missing_alpaca_credentials")
        return 1

    limits = RiskLimits(
        risk_per_trade_pct=settings.risk_per_trade_pct,
        daily_loss_limit_pct=settings.daily_loss_limit_pct,
        max_concurrent_positions=settings.max_concurrent_positions,
    )

    if args.date:
        days = [args.date] if is_trading_day(args.date) else []
        if not days:
            log.error("not_a_trading_day", date=args.date.isoformat())
            return 1
    else:
        # Most recent past trading day, or N back.
        today = datetime.now(ET).date()
        anchor = today - timedelta(days=1)
        while not is_trading_day(anchor):
            anchor = anchor - timedelta(days=1)
        days = trading_days_back(anchor, args.days)

    log.info("replay_starting", days=[d.isoformat() for d in days])
    all_trades: list[SimulatedTrade] = []
    for d in days:
        trades = replay_one_day(d, args.equity, limits, log, args.strategy)
        all_trades.extend(trades)

    print_report(all_trades)

    if not args.no_persist and all_trades:
        from datetime import datetime as _dt

        run_id = save_replay(
            days=[_dt(d.year, d.month, d.day, tzinfo=UTC) for d in days],
            equity=args.equity,
            watchlist=WATCHLIST,
            strategy=args.strategy,
            trades=all_trades,
        )
        log.info("replay_persisted", run_id=str(run_id), trades=len(all_trades))
        print(f"Saved replay run {run_id} ({len(all_trades)} trades). Open the dashboard to view.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
