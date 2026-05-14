"""Opening Range Breakout — 5-minute window on liquid large-caps.

Rules:
  - Opening range = high/low of the first five 1-min bars (9:30 ET through 9:34 ET inclusive).
  - Long entry when a 1-min bar closes above OR high AND the breakout bar's volume
    is at least 1.5x the average minute-volume of the OR.
  - Stop at OR low. Target at entry + (OR high - OR low) = 1R.
  - At most one entry per symbol per day.
  - No entries before 9:35 ET or after 11:00 ET.
  - If we missed any of the five OR bars, skip the symbol for the day.

These rules are intentionally simple. Variations (volume vs 20-day avg, multi-target
scaling, short side) are explicit TODOs for v0.2 — easier to debug one thing at a time.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, time
from decimal import Decimal

from trident.clock import ET
from trident.data.bars import Bar, BarStore
from trident.strategies.base import Signal

OR_START = time(9, 30)
OR_END = time(9, 35)        # exclusive; OR covers [9:30, 9:35)
LAST_ENTRY = time(11, 0)    # no entries at or after this
VOLUME_MULTIPLIER = Decimal("1.5")


@dataclass
class _DayState:
    or_bars: dict[time, Bar] = field(default_factory=dict)
    or_high: Decimal | None = None
    or_low: Decimal | None = None
    or_avg_volume: Decimal | None = None
    entered: bool = False
    skipped: bool = False  # set if we missed bars and gave up for the day


class OpeningRangeBreakout:
    name = "orb_5m"

    def __init__(self, symbols: list[str]) -> None:
        self._symbols = {s.upper() for s in symbols}
        self._state: dict[tuple[str, date], _DayState] = {}

    def _get_state(self, symbol: str, d: date) -> _DayState:
        key = (symbol, d)
        if key not in self._state:
            self._state[key] = _DayState()
        return self._state[key]

    def on_bar(self, bar: Bar, store: BarStore) -> Signal | None:
        if bar.symbol.upper() not in self._symbols:
            return None
        if bar.timeframe != "1min":
            return None

        bar_et = bar.ts.astimezone(ET)
        today = bar_et.date()
        t = bar_et.time()
        state = self._get_state(bar.symbol, today)

        # Phase 1: accumulate OR bars during [9:30, 9:35).
        if OR_START <= t < OR_END:
            state.or_bars[t] = bar
            return None

        # Past the OR window — finalize if we haven't.
        if state.or_high is None and not state.skipped:
            if len(state.or_bars) < 5:
                state.skipped = True
                return None
            highs = [b.high for b in state.or_bars.values()]
            lows = [b.low for b in state.or_bars.values()]
            vols = [Decimal(b.volume) for b in state.or_bars.values()]
            state.or_high = max(highs)
            state.or_low = min(lows)
            state.or_avg_volume = sum(vols, Decimal("0")) / Decimal(len(vols))

        if state.skipped or state.entered:
            return None
        if state.or_high is None or state.or_low is None or state.or_avg_volume is None:
            return None

        # Phase 2: look for the first breakout bar before LAST_ENTRY.
        if t >= LAST_ENTRY:
            return None
        if bar.close <= state.or_high:
            return None
        if Decimal(bar.volume) < state.or_avg_volume * VOLUME_MULTIPLIER:
            return None

        entry = bar.close
        stop = state.or_low
        or_range = state.or_high - state.or_low
        target = entry + or_range

        state.entered = True
        return Signal(
            ts=bar.ts,
            strategy=self.name,
            symbol=bar.symbol,
            side="long",
            entry_price=entry,
            stop_price=stop,
            target_price=target,
            meta={
                "or_high": str(state.or_high),
                "or_low": str(state.or_low),
                "or_avg_volume": str(state.or_avg_volume),
                "breakout_volume": bar.volume,
            },
        )

    def reset_for_day(self, d: date) -> None:
        """Drop state for days other than `d`. Call once at session start."""
        self._state = {k: v for k, v in self._state.items() if k[1] == d}
