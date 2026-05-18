"""Unit tests for AlpacaBarFeed shutdown.

No network: the alpaca-py StockDataStream is replaced by a fake that records
whether stop_ws() — the stream's designed clean-exit path — was awaited.
"""
from __future__ import annotations

from trident.data.bars import BarStore
from trident.data.feed import AlpacaBarFeed


class _FakeStream:
    """Stand-in for alpaca-py's StockDataStream — records the stop call."""

    def __init__(self) -> None:
        self.stop_ws_calls = 0

    async def stop_ws(self) -> None:
        self.stop_ws_calls += 1


def _feed() -> AlpacaBarFeed:
    return AlpacaBarFeed(
        api_key="k", api_secret="s", symbols=["SPY"], store=BarStore(), feed="iex"
    )


async def test_stop_is_noop_before_run() -> None:
    """stop() before run() has set up a client must not raise."""
    feed = _feed()
    await feed.stop()  # no _client yet — a clean no-op


async def test_stop_signals_the_stream() -> None:
    """stop() delegates to the stream's stop_ws(), its designed exit path."""
    feed = _feed()
    fake = _FakeStream()
    feed._client = fake  # simulate a started run()
    await feed.stop()
    assert fake.stop_ws_calls == 1
