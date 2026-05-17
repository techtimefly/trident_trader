"""Mini stock screener — scan the US-equity universe for the few filters that
matter and print a ranked table.

Filters (all optional; an omitted flag means 'no bound'):
  --min-price / --max-price   price band, in dollars. The max bound finds the
                              cheap stuff — `--max-price 1.00` lists sub-$1 names.
  --min-avg-volume            liquidity floor: minimum average daily share volume.
  --min-change / --max-change recent % change band over the lookback window.

A full-universe scan is SLOW. Alpaca lists several thousand tradeable US
equities and the price/volume data comes from batched daily-bar requests — a
handful of seconds per 100-symbol batch. `--limit` truncates the (alphabetically
sorted) universe so a scan finishes in a reasonable time; the honest tradeoff is
that a small `--limit` only sees the front of the alphabet. For a real
full-market sweep, raise `--limit` and expect it to take minutes.

"Average daily volume" is the simple mean of each symbol's daily bar volume over
the last `--lookback` trading days (default 20). "% change" is the move of the
latest close vs. the first close in that same window.

Examples:
    PYTHONPATH=src python scripts/screen.py --max-price 1.00 --min-avg-volume 1000000 --limit 500
    PYTHONPATH=src python scripts/screen.py --min-price 5 --max-price 50 --min-avg-volume 500000
    PYTHONPATH=src python scripts/screen.py --min-change 5 --limit 300 --no-persist
"""
from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation

from trident.audit.log import configure_logging, get_logger
from trident.screener.criteria import ScreenCriteria, ScreenResult
from trident.screener.data import build_candidates, fetch_universe
from trident.screener.engine import screen
from trident.screener.persistence import save_screen
from trident.settings import get_settings


def _decimal_arg(raw: str) -> Decimal:
    try:
        return Decimal(raw)
    except InvalidOperation as exc:  # pragma: no cover - argparse surfaces this
        raise argparse.ArgumentTypeError(f"not a number: {raw!r}") from exc


def _fmt_vol(v: int) -> str:
    if v >= 1_000_000:
        return f"{v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"{v / 1_000:.0f}K"
    return str(v)


def print_report(criteria: ScreenCriteria, result: ScreenResult, lookback: int) -> None:
    print("\n" + "=" * 72)
    print("STOCK SCREENER")
    print(f"  filters: {criteria.describe()}")
    print(f"  lookback: {lookback} trading days")
    print(f"  scanned {result.scanned} symbols  ->  {result.matched} matched")
    print("=" * 72)

    if not result.matches:
        print("\nNo symbols matched. Loosen the filters or widen --limit.\n")
        return

    print(f"\n{'#':>4}  {'Symbol':<8}{'Price':>12}{'Avg Vol':>12}{'Change %':>12}")
    print("-" * 52)
    for idx, c in enumerate(result.matches, start=1):
        print(
            f"{idx:>4}  {c.symbol:<8}"
            f"{('$' + format(c.price, 'f')):>12}"
            f"{_fmt_vol(c.avg_volume):>12}"
            f"{format(c.change_pct, '+f'):>12}"
        )
    print("-" * 52)
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description="Mini stock screener (price / volume / % change).")
    parser.add_argument("--min-price", type=_decimal_arg, help="Minimum latest price, dollars.")
    parser.add_argument(
        "--max-price",
        type=_decimal_arg,
        help="Maximum latest price, dollars. Use a low value (e.g. 1.00) to find cheap stocks.",
    )
    parser.add_argument(
        "--min-avg-volume",
        type=int,
        help="Minimum average daily share volume over the lookback window.",
    )
    parser.add_argument(
        "--min-change",
        type=_decimal_arg,
        help="Minimum recent %% change over the lookback window.",
    )
    parser.add_argument(
        "--max-change",
        type=_decimal_arg,
        help="Maximum recent %% change over the lookback window.",
    )
    parser.add_argument(
        "--lookback",
        type=int,
        default=20,
        help="Trading-day window for avg volume and %% change. Default: 20.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=500,
        help="Cap the universe size (alphabetical). Default: 500. Use -1 for the full universe.",
    )
    parser.add_argument(
        "--no-persist",
        action="store_true",
        help="Skip writing the run to the database (the dashboard won't see it).",
    )
    args = parser.parse_args()

    configure_logging()
    log = get_logger("screen")
    settings = get_settings()
    if not settings.alpaca_api_key:
        log.error("missing_alpaca_credentials")
        return 1
    if args.lookback < 2:
        log.error("invalid_lookback", lookback=args.lookback)
        return 1

    criteria = ScreenCriteria(
        min_price=args.min_price,
        max_price=args.max_price,
        min_avg_volume=args.min_avg_volume,
        min_change_pct=args.min_change,
        max_change_pct=args.max_change,
    )

    limit = None if args.limit is not None and args.limit < 0 else args.limit
    log.info("screen_starting", limit=limit, lookback=args.lookback)
    print(f"Fetching universe (limit={limit if limit is not None else 'all'})...")
    universe = fetch_universe(limit=limit)
    print(f"Scanning {len(universe)} symbols for daily bars (this can take a while)...")
    candidates = build_candidates(universe, lookback_days=args.lookback)

    result = screen(candidates, criteria)
    print_report(criteria, result, args.lookback)

    if not args.no_persist:
        run_id = save_screen(
            result=result,
            universe_size=len(universe),
            lookback_days=args.lookback,
        )
        log.info("screen_persisted", run_id=str(run_id), matched=result.matched)
        print(f"Saved screen run {run_id} ({result.matched} matches). Open the dashboard to view.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
