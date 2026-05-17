"""Alpaca-backed data layer for the screener — the only place that touches the
network. Keeps all I/O at the edge so the engine stays a pure function.

Two responsibilities:

1. **Universe** — :func:`fetch_universe` pulls the tradeable US-equity symbols
   from Alpaca's ``TradingClient.get_all_assets`` and filters to active,
   tradable, fractionable-or-not common shares.
2. **Market facts** — :func:`build_candidates` fetches daily bars in batches via
   ``StockHistoricalDataClient`` and reduces each symbol to a
   :class:`~trident.screener.criteria.ScreenCandidate` (latest price, average
   daily volume, and recent % change over the lookback window).

A full-universe scan is *slow* — see the module docstring of ``scripts/screen.py``
and the ``--limit`` flag. The historical client is built exactly like
``scripts/replay.py:fetch_minute_bars``: ``feed=DataFeed.IEX`` because the free
tier 403s on SIP.

Money is ``Decimal`` end to end — bar prices are stringified before the Decimal
constructor so no float ever enters the pipeline.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from trident.audit.log import get_logger
from trident.screener.criteria import ScreenCandidate
from trident.settings import get_settings

log = get_logger("screener.data")

# How many symbols to put in one StockBarsRequest. Alpaca accepts large symbol
# lists, but smaller batches fail softer (one bad batch loses fewer symbols) and
# keep memory bounded. 100 is a pragmatic middle ground.
BATCH_SIZE = 100


def _trading_client() -> Any:
    settings = get_settings()
    from alpaca.trading.client import TradingClient

    return TradingClient(
        api_key=settings.alpaca_api_key,
        secret_key=settings.alpaca_api_secret,
        paper=True,
    )


def _data_client() -> Any:
    """Construct the historical bar client — same recipe as replay.py."""
    settings = get_settings()
    from alpaca.data.historical import StockHistoricalDataClient

    return StockHistoricalDataClient(settings.alpaca_api_key, settings.alpaca_api_secret)


def fetch_universe(limit: int | None = None) -> list[str]:
    """Return the active, tradable US-equity symbols from Alpaca, sorted.

    Filters ``get_all_assets`` to ``status == active``, ``tradable == True``,
    and the US-equity asset class. ``limit`` truncates the (sorted) list so a
    scan stays tractable — the full universe is several thousand symbols and a
    daily-bar scan over all of them is many minutes of API calls.

    Raises whatever the Alpaca SDK raises; callers (the CLI) handle that.
    """
    from alpaca.trading.enums import AssetClass, AssetStatus
    from alpaca.trading.requests import GetAssetsRequest

    req = GetAssetsRequest(status=AssetStatus.ACTIVE, asset_class=AssetClass.US_EQUITY)
    assets = _trading_client().get_all_assets(req)
    symbols = sorted(
        str(a.symbol)
        for a in assets
        if getattr(a, "tradable", False)
        # Skip symbols Alpaca can't price as ordinary equities (warrants,
        # units and rights carry punctuation in the symbol).
        and "." not in str(a.symbol)
        and "/" not in str(a.symbol)
    )
    log.info("screener_universe_fetched", count=len(symbols), limit=limit)
    if limit is not None and limit >= 0:
        return symbols[:limit]
    return symbols


def _batches(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _candidate_from_bars(symbol: str, bars: list[Any]) -> ScreenCandidate | None:
    """Reduce one symbol's daily bars to a candidate, or None if unusable.

    - ``price`` is the latest (most recent) daily close.
    - ``avg_volume`` is the mean of every bar's volume over the window, floored
      to an int.
    - ``change_pct`` is ``(last_close - first_close) / first_close * 100`` — the
      move across the whole lookback window. A single-bar window yields 0%.

    Returns None when there are no bars or the first close is zero (a degenerate
    symbol we cannot compute a % change for).
    """
    if not bars:
        return None
    ordered = sorted(bars, key=lambda b: b.timestamp)
    first_close = Decimal(str(ordered[0].close))
    last_close = Decimal(str(ordered[-1].close))
    if first_close <= 0:
        return None
    total_volume = sum(int(b.volume) for b in ordered)
    avg_volume = total_volume // len(ordered)
    change_pct = (last_close - first_close) / first_close * Decimal("100")
    return ScreenCandidate(
        symbol=symbol,
        price=last_close,
        avg_volume=avg_volume,
        change_pct=change_pct.quantize(Decimal("0.01")),
    )


def build_candidates(
    symbols: list[str], lookback_days: int = 20, batch_size: int = BATCH_SIZE
) -> list[ScreenCandidate]:
    """Fetch daily bars for ``symbols`` and reduce each to a ScreenCandidate.

    ``lookback_days`` is the trading-day window the average volume and % change
    are measured over. Symbols with no bars in the window are dropped silently
    (delisted, brand-new, or simply illiquid enough to have no IEX prints).

    Requests are issued in batches of ``batch_size``; a batch that errors is
    logged and skipped rather than failing the whole scan — a screener is
    outer-ring and degrades rather than aborting.
    """
    from alpaca.data.enums import DataFeed
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

    settings = get_settings()
    feed = DataFeed.IEX if settings.alpaca_data_feed.lower() == "iex" else DataFeed.SIP
    client = _data_client()

    # Pad the calendar window generously — weekends and holidays mean a
    # 20-trading-day lookback spans ~30 calendar days. Over-fetching is cheap;
    # only the most recent `lookback_days` bars are used for the average.
    end = datetime.now(UTC)
    start = end - timedelta(days=lookback_days * 2 + 10)

    out: list[ScreenCandidate] = []
    for batch in _batches(symbols, batch_size):
        req = StockBarsRequest(
            symbol_or_symbols=batch,
            start=start,
            end=end,
            timeframe=TimeFrame(1, TimeFrameUnit.Day),
            feed=feed,
        )
        try:
            resp = client.get_stock_bars(req)
        except Exception:
            log.exception("screener_batch_failed", symbols=len(batch))
            continue
        for sym in batch:
            bars = list(resp.data.get(sym, []))
            # Keep only the most recent `lookback_days` daily bars.
            window = sorted(bars, key=lambda b: b.timestamp)[-lookback_days:]
            candidate = _candidate_from_bars(sym, window)
            if candidate is not None:
                out.append(candidate)
    log.info("screener_candidates_built", requested=len(symbols), built=len(out))
    return out
