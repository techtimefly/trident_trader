"""Mini stock screener — scan a US-equity universe against a managed filter set
and print a ranked table.

The filters are a *managed* config: named presets in the ``screen_presets``
table, exactly one active (edit them in the dashboard). By default this script
runs the active preset; ``--preset NAME`` runs a specific one, and any filter
flag below overrides that preset's value for this run.

Filters (an omitted flag leaves the preset's value untouched):
  --min-price / --max-price             price band, in dollars.
  --min-avg-volume                      liquidity floor: min avg daily volume.
  --min-change / --max-change           recent % change band over the lookback.
  --min-market-cap / --max-market-cap   market-cap band, in dollars (FMP).
  --sector / --exchange                 allow-list entries, repeatable (FMP).

Universe: when ``FMP_API_KEY`` is set, the screener asks Financial Modeling Prep
for a criteria-matched universe in one call (``--max-symbols`` caps it). Without
a key it falls back to the alphabetical Alpaca asset list (``--limit`` caps it,
``-1`` for the whole list) — and the market-cap / sector / exchange filters,
which only FMP can satisfy, are skipped for that run with a notice.

Price / volume / % change are always computed from Alpaca daily bars over the
last ``--lookback`` trading days; the FMP universe call is only a fast
pre-filter.

Examples:
    PYTHONPATH=src python scripts/screen.py
    PYTHONPATH=src python scripts/screen.py --preset "Large-cap tech"
    PYTHONPATH=src python scripts/screen.py --min-market-cap 1000000000 --sector Technology
    PYTHONPATH=src python scripts/screen.py --max-price 1.00 --save-preset "Sub-dollar"
"""
from __future__ import annotations

import argparse
from dataclasses import replace
from decimal import Decimal, InvalidOperation
from typing import Any

from trident.audit.log import configure_logging, get_logger
from trident.persistence.screen_presets_store import (
    ScreenPresetRecord,
    activate_preset,
    list_presets,
    upsert_preset,
)
from trident.screener.criteria import ScreenCriteria, ScreenResult
from trident.screener.data import build_candidates, resolve_universe
from trident.screener.engine import screen
from trident.screener.fmp import is_configured
from trident.screener.persistence import save_screen
from trident.screener.presets import resolve_screen_criteria
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
        print("\nNo symbols matched. Loosen the filters or widen the universe.\n")
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


def _find_preset(name: str) -> ScreenPresetRecord | None:
    """Look up a preset by exact name, or None if there is no such preset."""
    return next((p for p in list_presets() if p.name == name), None)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Mini stock screener — runs a managed, customizable filter preset."
    )
    parser.add_argument("--preset", help="Run this named preset instead of the active one.")
    parser.add_argument("--min-price", type=_decimal_arg, help="Minimum latest price, dollars.")
    parser.add_argument("--max-price", type=_decimal_arg, help="Maximum latest price, dollars.")
    parser.add_argument(
        "--min-avg-volume",
        type=int,
        help="Minimum average daily share volume over the lookback window.",
    )
    parser.add_argument(
        "--min-change", type=_decimal_arg, help="Minimum recent %% change over the lookback."
    )
    parser.add_argument(
        "--max-change", type=_decimal_arg, help="Maximum recent %% change over the lookback."
    )
    parser.add_argument(
        "--min-market-cap", type=int, help="Minimum market capitalization, dollars (needs FMP)."
    )
    parser.add_argument(
        "--max-market-cap", type=int, help="Maximum market capitalization, dollars (needs FMP)."
    )
    parser.add_argument(
        "--sector",
        action="append",
        help="Allowed sector (repeatable), e.g. --sector Technology (needs FMP).",
    )
    parser.add_argument(
        "--exchange",
        action="append",
        help="Allowed exchange short code (repeatable), e.g. --exchange NASDAQ (needs FMP).",
    )
    parser.add_argument(
        "--lookback",
        type=int,
        help="Trading-day window for avg volume and %% change. Default: the preset's value.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=500,
        help="Cap the fallback Alpaca universe (alphabetical). Default: 500. Use -1 for all.",
    )
    parser.add_argument(
        "--max-symbols",
        type=int,
        default=500,
        help="Cap the symbols the FMP universe yields to the bar scan. Default: 500.",
    )
    parser.add_argument(
        "--save-preset",
        help="Save the effective criteria as a named preset and activate it.",
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
        print("ALPACA_API_KEY is not set — the screener needs Alpaca for daily bars.")
        return 1

    # Base criteria + lookback: a named preset, or the active one (with a
    # static fallback when no preset exists or the DB is down).
    if args.preset:
        try:
            match = _find_preset(args.preset)
        except Exception:
            log.exception("preset_lookup_failed", preset=args.preset)
            print("Could not load presets — check the database connection.")
            return 1
        if match is None:
            log.error("unknown_preset", preset=args.preset)
            print(f"No screen preset named {args.preset!r}.")
            return 1
        base_criteria, base_lookback = match.criteria, match.lookback_days
    else:
        base_criteria, base_lookback = resolve_screen_criteria()

    # Overlay any explicit filter flags onto the base preset.
    overrides: dict[str, Any] = {}
    if args.min_price is not None:
        overrides["min_price"] = args.min_price
    if args.max_price is not None:
        overrides["max_price"] = args.max_price
    if args.min_avg_volume is not None:
        overrides["min_avg_volume"] = args.min_avg_volume
    if args.min_change is not None:
        overrides["min_change_pct"] = args.min_change
    if args.max_change is not None:
        overrides["max_change_pct"] = args.max_change
    if args.min_market_cap is not None:
        overrides["min_market_cap"] = args.min_market_cap
    if args.max_market_cap is not None:
        overrides["max_market_cap"] = args.max_market_cap
    if args.sector is not None:
        overrides["sectors"] = tuple(args.sector)
    if args.exchange is not None:
        overrides["exchanges"] = tuple(args.exchange)
    criteria = replace(base_criteria, **overrides) if overrides else base_criteria

    lookback = args.lookback if args.lookback is not None else base_lookback
    if lookback < 2:
        log.error("invalid_lookback", lookback=lookback)
        print("--lookback (or the preset's lookback) must be at least 2.")
        return 1

    # Save the effective criteria as a preset if asked — before any FMP-only
    # bounds are stripped, so the preset stays correct once a key is added.
    if args.save_preset:
        try:
            preset_id = upsert_preset(args.save_preset, criteria, lookback)
            activate_preset(preset_id)
        except Exception:
            log.exception("save_preset_failed", preset=args.save_preset)
            print("Could not save the preset — check the database connection.")
            return 1
        log.info("preset_saved", preset=args.save_preset, preset_id=str(preset_id))
        print(f"Saved and activated preset {args.save_preset!r}.")

    log.info("screen_starting", lookback=lookback, fmp_configured=is_configured())
    print("Resolving the screen universe...")
    symbols, fmp_meta = resolve_universe(
        criteria,
        max_symbols=args.max_symbols,
        fallback_limit=(None if args.limit < 0 else args.limit),
    )

    # FMP supplies the market-cap / sector / exchange metadata. With no FMP
    # metadata (no key, or the FMP call failed) the engine would reject every
    # candidate for those bounds, so strip them for this run — the screen still
    # produces price / volume / % change results (the saved preset keeps them).
    run_criteria = criteria
    has_fmp_filters = (
        criteria.min_market_cap is not None
        or criteria.max_market_cap is not None
        or bool(criteria.sectors)
        or bool(criteria.exchanges)
    )
    if not fmp_meta and has_fmp_filters:
        if is_configured():
            print(
                "FMP screener unavailable (check your FMP plan/key) — market "
                "cap / sector / exchange filters skipped for this run."
            )
        else:
            print(
                "FMP not configured — market cap / sector / exchange filters "
                "skipped (set FMP_API_KEY to use them)."
            )
        run_criteria = replace(
            criteria,
            min_market_cap=None,
            max_market_cap=None,
            sectors=(),
            exchanges=(),
        )

    print(f"Scanning {len(symbols)} symbols for daily bars (this can take a while)...")
    candidates = build_candidates(symbols, lookback_days=lookback, fmp_meta=fmp_meta)

    result = screen(candidates, run_criteria)
    print_report(run_criteria, result, lookback)

    if not args.no_persist:
        run_id = save_screen(
            result=result,
            universe_size=len(symbols),
            lookback_days=lookback,
        )
        log.info("screen_persisted", run_id=str(run_id), matched=result.matched)
        print(f"Saved screen run {run_id} ({result.matched} matches). Open the dashboard to view.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
