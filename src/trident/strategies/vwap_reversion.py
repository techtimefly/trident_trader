"""VWAP Mean-Reversion -- intraday stretch-and-snap on 1-min bars.

Long signal:  bar closes >= BAND_PCT% below VWAP -> expect reversion up to VWAP.
Short signal: bar closes >= BAND_PCT% above VWAP -> expect reversion down to VWAP.

Trade geometry (both sides):
  VWAP is the "fair value" anchor.  Stop is equidistant on the far side:
  Long:  stop = 2*entry - VWAP (same distance below entry as entry is below VWAP).
  Short: stop = 2*entry - VWAP (same distance above entry as entry is above VWAP).
  Target for both sides = current VWAP.  R:R = 1:1 by construction.

Window: 9:45-14:30 ET.  9:30-9:44 bars feed VWAP only (>=15 bars of history
before the first signal).  No entries from 14:30 onward -- insufficient time for
intraday mean-reversion to materialize before the close.

At most one entry per symbol per day (avoids averaging into a trend).

Note: the gate's no_entry_after is a wider outer backstop.  Until item 2.6
widens the gate default, only signals before 11:00 ET will be gate-approved.
The strategy window (14:30) reflects the correct intraday intent.

Source: VWAP mean-reversion is documented in Brian Shannon, "Technical Analysis
Using Multiple Timeframes" (2008) and widely used in practitioner literature as
a disciplined intraday fade.  BAND_PCT = 1.5% is a starting point; evaluate
against out-of-sample data before any tuning.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time
from decimal import Decimal

from trident.clock import ET
from trident.data.bars import Bar, BarStore
from trident.strategies.base import Signal

# Price must be at least this far from VWAP (as a fraction) to trigger an entry.
BAND_PCT = Decimal("0.015")

# Entry window: wait for ≥15 bars of VWAP history; stop before last 90 min.
NO_ENTRY_BEFORE = time(9, 45)
NO_ENTRY_AFTER = time(14, 30)


@dataclass
class _DayState:
    cum_pv: Decimal = Decimal("0")  # cumulative (typical price x volume)
    cum_v: Decimal = Decimal("0")   # cumulative volume
    entered: bool = False

    @property
    def vwap(self) -> Decimal | None:
        return self.cum_pv / self.cum_v if self.cum_v > Decimal("0") else None


class VWAPReversion:
    name = "vwap_reversion"

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

        # Update running VWAP: typical price = (high + low + close) / 3.
        typical = (bar.high + bar.low + bar.close) / Decimal("3")
        state.cum_pv += typical * Decimal(bar.volume)
        state.cum_v += Decimal(bar.volume)

        if state.entered or t < NO_ENTRY_BEFORE or t >= NO_ENTRY_AFTER:
            return None

        vwap = state.vwap
        if vwap is None or vwap <= Decimal("0"):
            return None

        entry = bar.close
        if entry <= Decimal("0"):
            return None

        lower_band = vwap * (Decimal("1") - BAND_PCT)
        upper_band = vwap * (Decimal("1") + BAND_PCT)

        if entry < lower_band:
            # Long: price stretched below VWAP — fade back to VWAP.
            stop = entry - (vwap - entry)   # symmetric: stop same distance below entry
            target = vwap
            if stop <= Decimal("0") or target <= entry:
                return None
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
                    "vwap": str(vwap),
                    "band_pct": str(BAND_PCT),
                    "stretch_pct": str(
                        ((vwap - entry) / vwap * Decimal("100")).quantize(Decimal("0.01"))
                    ),
                },
            )

        if entry > upper_band:
            # Short: price stretched above VWAP — fade back to VWAP.
            stop = entry + (entry - vwap)   # symmetric: stop same distance above entry
            target = vwap
            if target >= entry or stop <= entry:
                return None
            state.entered = True
            return Signal(
                ts=bar.ts,
                strategy=self.name,
                symbol=bar.symbol,
                side="short",
                entry_price=entry,
                stop_price=stop,
                target_price=target,
                meta={
                    "vwap": str(vwap),
                    "band_pct": str(BAND_PCT),
                    "stretch_pct": str(
                        ((entry - vwap) / vwap * Decimal("100")).quantize(Decimal("0.01"))
                    ),
                },
            )

        return None

    def reset_for_day(self, d: date) -> None:
        """Drop state for days other than `d`. Call once at session start."""
        self._state = {k: v for k, v in self._state.items() if k[1] == d}
