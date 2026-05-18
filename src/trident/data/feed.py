from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from trident.audit.log import get_logger
from trident.data.bars import Bar, BarStore

if TYPE_CHECKING:
    from alpaca.data.live import StockDataStream

log = get_logger("data.feed")

BarHandler = Callable[[Bar], Awaitable[None]]


class AlpacaBarFeed:
    """Wraps the Alpaca WebSocket client to deliver normalized Bar objects.

    The Alpaca SDK gives us per-minute aggregated bars; we forward each closed bar
    to a handler and into the in-memory BarStore. No persistence here — that's a
    separate writer so a slow DB never blocks the data path.
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        symbols: Sequence[str],
        store: BarStore,
        feed: str = "iex",
    ) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._symbols = list(symbols)
        self._store = store
        self._feed = feed
        self._handlers: list[BarHandler] = []
        self._client: StockDataStream | None = None  # lazy import in run()

    def on_bar(self, handler: BarHandler) -> None:
        self._handlers.append(handler)

    async def _handle_raw_bar(self, raw: object) -> None:
        # alpaca-py delivers a `Bar` model; we normalize to ours.
        bar = Bar(
            symbol=getattr(raw, "symbol"),
            ts=getattr(raw, "timestamp").astimezone(UTC),
            timeframe="1min",
            open=Decimal(str(getattr(raw, "open"))),
            high=Decimal(str(getattr(raw, "high"))),
            low=Decimal(str(getattr(raw, "low"))),
            close=Decimal(str(getattr(raw, "close"))),
            volume=int(getattr(raw, "volume")),
        )
        self._store.append(bar)
        for h in self._handlers:
            try:
                await h(bar)
            except Exception:
                log.exception("bar_handler_error", symbol=bar.symbol)

    async def run(self) -> None:
        from alpaca.data.enums import DataFeed
        from alpaca.data.live import StockDataStream

        feed_enum = DataFeed.IEX if self._feed.lower() == "iex" else DataFeed.SIP
        self._client = StockDataStream(
            self._api_key, self._api_secret, feed=feed_enum
        )
        self._client.subscribe_bars(self._handle_raw_bar, *self._symbols)
        log.info("feed_starting", symbols=self._symbols, feed=self._feed)
        await self._client._run_forever()

    async def stop(self) -> None:
        """Signal the websocket loop to exit cleanly so ``run()`` returns.

        Cancelling the ``run()`` task is not enough: alpaca-py's
        ``_run_forever`` catches broad exceptions to reconnect, so a bare
        cancel leaves the stream alive and ``asyncio.run()`` hanging during
        shutdown. ``stop_ws()`` clears the stream's ``_should_run`` flag and
        enqueues a stop message — the loop's designed exit path, which also
        closes the socket. A no-op if ``run()`` has not started yet.
        """
        if self._client is not None:
            await self._client.stop_ws()


def synthetic_bars(
    symbol: str,
    start: datetime,
    count: int,
    open_price: Decimal = Decimal("100"),
) -> list[Bar]:
    """Used by tests and shadow runs to fabricate a bar stream."""
    out: list[Bar] = []
    price = open_price
    for i in range(count):
        ts = start + timedelta(minutes=i)
        out.append(
            Bar(
                symbol=symbol,
                ts=ts,
                timeframe="1min",
                open=price,
                high=price + Decimal("0.1"),
                low=price - Decimal("0.1"),
                close=price + Decimal("0.05"),
                volume=10_000,
            )
        )
        price += Decimal("0.05")
    return out
