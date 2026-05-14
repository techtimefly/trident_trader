from __future__ import annotations

from sqlalchemy.dialects.postgresql import insert

from trident.data.bars import Bar
from trident.persistence.models import Bar as BarRow
from trident.persistence.session import session_scope


def persist_bar(bar: Bar) -> None:
    """Upsert by (symbol, ts, timeframe). Idempotent under retries / replay."""
    with session_scope() as s:
        stmt = insert(BarRow).values(
            symbol=bar.symbol,
            ts=bar.ts,
            timeframe=bar.timeframe,
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            volume=bar.volume,
        )
        stmt = stmt.on_conflict_do_nothing(constraint="uq_bars_symbol_ts_tf")
        s.execute(stmt)


def persist_bars(bars: list[Bar]) -> int:
    if not bars:
        return 0
    with session_scope() as s:
        rows = [
            {
                "symbol": b.symbol,
                "ts": b.ts,
                "timeframe": b.timeframe,
                "open": b.open,
                "high": b.high,
                "low": b.low,
                "close": b.close,
                "volume": b.volume,
            }
            for b in bars
        ]
        stmt = insert(BarRow).values(rows)
        stmt = stmt.on_conflict_do_nothing(constraint="uq_bars_symbol_ts_tf")
        s.execute(stmt)
    return len(bars)
