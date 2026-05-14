"""Backfill the last N trading days of daily bars for the watchlist.

Useful as a one-off after the DB is set up — gives the strategy and any future
backtests some history to look at.

Usage:
    PYTHONPATH=src python3 scripts/backfill_daily.py [days=60]
"""
from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from trident.audit.log import configure_logging, get_logger
from trident.data.bars import Bar
from trident.data.persistence import persist_bars
from trident.settings import get_settings

WATCHLIST = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMD"]


def main() -> int:
    configure_logging()
    log = get_logger("backfill")
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    settings = get_settings()
    if not settings.alpaca_api_key:
        log.error("missing_alpaca_credentials")
        return 1

    from alpaca.data.enums import DataFeed
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    client = StockHistoricalDataClient(settings.alpaca_api_key, settings.alpaca_api_secret)

    feed = DataFeed.IEX if settings.alpaca_data_feed.lower() == "iex" else DataFeed.SIP

    end = datetime.now(UTC)
    start = end - timedelta(days=days * 2)  # over-fetch to account for weekends/holidays

    total = 0
    for symbol in WATCHLIST:
        req = StockBarsRequest(
            symbol_or_symbols=symbol,
            start=start,
            end=end,
            timeframe=TimeFrame.Day,
            feed=feed,
        )
        bars_resp = client.get_stock_bars(req)
        bars = bars_resp.data.get(symbol, [])
        normalized = [
            Bar(
                symbol=symbol,
                ts=b.timestamp.astimezone(UTC),
                timeframe="1day",
                open=Decimal(str(b.open)),
                high=Decimal(str(b.high)),
                low=Decimal(str(b.low)),
                close=Decimal(str(b.close)),
                volume=int(b.volume),
            )
            for b in bars
        ]
        persisted = persist_bars(normalized)
        log.info("backfilled", symbol=symbol, count=persisted)
        total += persisted

    log.info("backfill_complete", total=total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
