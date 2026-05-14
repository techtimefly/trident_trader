from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class Bar:
    symbol: str
    ts: datetime  # bar close time (UTC)
    timeframe: str  # e.g. "1min", "5min"
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int

    def to_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "ts": self.ts.isoformat(),
            "timeframe": self.timeframe,
            "open": str(self.open),
            "high": str(self.high),
            "low": str(self.low),
            "close": str(self.close),
            "volume": self.volume,
        }


class BarStore:
    """In-memory ring buffer per (symbol, timeframe). Used by strategies for fast lookback.

    Persisting bars to Postgres is a separate concern; the strategy loop reads from here.
    """

    def __init__(self, maxlen: int = 600) -> None:
        self._buffers: dict[tuple[str, str], deque[Bar]] = defaultdict(
            lambda: deque(maxlen=maxlen)
        )
        self._maxlen = maxlen

    def append(self, bar: Bar) -> None:
        key = (bar.symbol, bar.timeframe)
        buf = self._buffers[key]
        if buf and buf[-1].ts >= bar.ts:
            return
        buf.append(bar)

    def recent(self, symbol: str, timeframe: str, n: int) -> list[Bar]:
        buf = self._buffers.get((symbol, timeframe))
        if not buf:
            return []
        if n >= len(buf):
            return list(buf)
        return list(buf)[-n:]

    def latest(self, symbol: str, timeframe: str) -> Bar | None:
        buf = self._buffers.get((symbol, timeframe))
        return buf[-1] if buf else None

    def bars_between(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> list[Bar]:
        buf = self._buffers.get((symbol, timeframe))
        if not buf:
            return []
        return [b for b in buf if start <= b.ts <= end]
