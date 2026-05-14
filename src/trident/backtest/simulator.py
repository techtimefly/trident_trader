"""Pure-function fill simulator used by the replay script.

This is intentionally idealistic — it does not model slippage, fees, or partial
fills. Its purpose is "did the strategy generate trades that would have made
money on this day?" not "what is the true expected P&L of this strategy?" The
honest backtest harness (with bid/ask spread sampling, commission, walk-forward)
lands in v0.3.

Assumptions documented here so future-me does not get fooled:
  - The breakout bar's close is the entry fill price. Real life would have
    slippage above this on a long entry.
  - When a single bar's high reaches the target AND its low reaches the stop,
    we pessimistically count it as a stop hit. Reality could be either.
  - EOD close uses the last bar of the day at its close price. Real EOD flatten
    is a market order at 15:55 ET that fills somewhere near the bid.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from trident.data.bars import Bar
from trident.strategies.base import Signal


@dataclass(frozen=True)
class SimulatedTrade:
    signal: Signal
    qty: int
    entry_price: Decimal
    exit_reason: str  # "target" | "stop" | "eod"
    exit_price: Decimal
    exit_ts_iso: str
    pnl: Decimal

    @property
    def r_multiple(self) -> Decimal:
        """Realized P&L expressed in R (risk units)."""
        risk = abs(self.entry_price - self.signal.stop_price)
        if risk == 0:
            return Decimal("0")
        per_share = self.exit_price - self.entry_price
        if self.signal.side == "short":
            per_share = -per_share
        return per_share / risk


def simulate_trade(signal: Signal, qty: int, subsequent_bars: list[Bar]) -> SimulatedTrade | None:
    """Walk forward through bars (same symbol, after signal.ts) and decide when the
    trade would have exited. Returns None if there are no follow-up bars."""

    same_symbol = [b for b in subsequent_bars if b.symbol == signal.symbol and b.ts > signal.ts]
    if not same_symbol:
        return None

    entry = signal.entry_price
    stop = signal.stop_price
    target = signal.target_price

    for bar in same_symbol:
        if signal.side == "long":
            hit_stop = bar.low <= stop
            hit_target = bar.high >= target
            if hit_stop:  # conservative: stop wins ties
                return SimulatedTrade(
                    signal=signal,
                    qty=qty,
                    entry_price=entry,
                    exit_reason="stop",
                    exit_price=stop,
                    exit_ts_iso=bar.ts.isoformat(),
                    pnl=(stop - entry) * qty,
                )
            if hit_target:
                return SimulatedTrade(
                    signal=signal,
                    qty=qty,
                    entry_price=entry,
                    exit_reason="target",
                    exit_price=target,
                    exit_ts_iso=bar.ts.isoformat(),
                    pnl=(target - entry) * qty,
                )
        else:  # short
            hit_stop = bar.high >= stop
            hit_target = bar.low <= target
            if hit_stop:
                return SimulatedTrade(
                    signal=signal,
                    qty=qty,
                    entry_price=entry,
                    exit_reason="stop",
                    exit_price=stop,
                    exit_ts_iso=bar.ts.isoformat(),
                    pnl=(entry - stop) * qty,
                )
            if hit_target:
                return SimulatedTrade(
                    signal=signal,
                    qty=qty,
                    entry_price=entry,
                    exit_reason="target",
                    exit_price=target,
                    exit_ts_iso=bar.ts.isoformat(),
                    pnl=(entry - target) * qty,
                )

    # No exit hit during the session → EOD flatten at the last bar's close.
    last = same_symbol[-1]
    per_share = last.close - entry if signal.side == "long" else entry - last.close
    return SimulatedTrade(
        signal=signal,
        qty=qty,
        entry_price=entry,
        exit_reason="eod",
        exit_price=last.close,
        exit_ts_iso=last.ts.isoformat(),
        pnl=per_share * qty,
    )
