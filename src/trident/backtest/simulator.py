"""Fill simulator for the replay + backtest harnesses.

The simulator walks forward through a day's bars and decides when a trade would
have exited (stop, target, or end-of-day flatten). Whether the result is
*idealistic* or *honest* depends entirely on the :class:`CostModel` passed in:

  - ``costs=ZERO_COST`` (the default) — fills at exact prices, no fees. This is
    the idealistic mode used by ``scripts/replay.py`` to answer "did the strategy
    generate trades on this day?"
  - a non-zero ``CostModel`` — slippage and fees applied; used by
    ``scripts/backtest.py`` to estimate true expected P&L.

Assumptions documented here so future-me does not get fooled:
  - The breakout bar's close is the *intended* entry price; a market/stop entry
    fills slightly worse (slippage).
  - When a single bar's high reaches the target AND its low reaches the stop, we
    pessimistically count it as a stop hit. Reality could be either.
  - A stop and the EOD flatten are market orders, so they slip against the
    trader. A target is a resting limit order, so it fills at the exact target
    price (or not at all) — no slippage on target exits.
  - EOD close uses the last bar of the day at its close price.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from trident.backtest.costs import (
    ZERO_COST,
    CostModel,
    apply_slippage,
    per_share_fee,
    regulatory_fee,
)
from trident.data.bars import Bar
from trident.strategies.base import Signal


@dataclass(frozen=True)
class SimulatedTrade:
    signal: Signal
    qty: int
    entry_price: Decimal  # actual fill, post-slippage
    exit_reason: str  # "target" | "stop" | "eod"
    exit_price: Decimal  # actual fill, post-slippage (exact for a target exit)
    exit_ts_iso: str
    pnl: Decimal  # net: gross P&L minus fees — the headline number
    gross_pnl: Decimal = Decimal("0")  # P&L from fill prices only, before fees
    entry_fee: Decimal = Decimal("0")
    exit_fee: Decimal = Decimal("0")
    ideal_entry_price: Decimal = Decimal("0")  # signal's intended (pre-slippage) entry

    @property
    def r_multiple(self) -> Decimal:
        """Gross P&L in units of *planned* risk.

        Risk is measured from the intended (pre-slippage) entry to the stop, and
        the numerator is gross P&L — so R is a clean price-geometry measure that
        excludes fees. The net ``pnl`` field is the one that includes both
        slippage and fees.
        """
        ideal = self.ideal_entry_price or self.entry_price
        risk = abs(ideal - self.signal.stop_price)
        if risk == 0 or self.qty == 0:
            return Decimal("0")
        return self.gross_pnl / (Decimal(self.qty) * risk)


def _build_trade(
    *,
    signal: Signal,
    qty: int,
    exit_reason: str,
    ideal_exit_price: Decimal,
    exit_ts_iso: str,
    exit_slips: bool,
    costs: CostModel,
) -> SimulatedTrade:
    """Assemble a fully-costed :class:`SimulatedTrade` for one exit.

    The entry always slips (market/stop entry). The exit slips only when
    ``exit_slips`` is true — stop and EOD exits are market orders; a target exit
    is a limit and fills at the exact price.
    """
    side = signal.side
    ideal_entry = signal.entry_price

    entry_fill = apply_slippage(ideal_entry, side, "enter", costs)
    exit_fill = (
        apply_slippage(ideal_exit_price, side, "exit", costs)
        if exit_slips
        else ideal_exit_price
    )

    if side == "long":
        gross = (exit_fill - entry_fill) * Decimal(qty)
    else:
        gross = (entry_fill - exit_fill) * Decimal(qty)

    # Broker commission applies to both legs; SEC/TAF to the sell leg only.
    # A long entry buys / a long exit sells; a short entry sells / a short exit buys.
    entry_is_sell = side == "short"
    exit_is_sell = side == "long"
    entry_fee = per_share_fee(qty, costs) + regulatory_fee(
        entry_fill * Decimal(qty), qty, entry_is_sell, costs
    )
    exit_fee = per_share_fee(qty, costs) + regulatory_fee(
        exit_fill * Decimal(qty), qty, exit_is_sell, costs
    )

    return SimulatedTrade(
        signal=signal,
        qty=qty,
        entry_price=entry_fill,
        exit_reason=exit_reason,
        exit_price=exit_fill,
        exit_ts_iso=exit_ts_iso,
        pnl=gross - entry_fee - exit_fee,
        gross_pnl=gross,
        entry_fee=entry_fee,
        exit_fee=exit_fee,
        ideal_entry_price=ideal_entry,
    )


def simulate_trade(
    signal: Signal,
    qty: int,
    subsequent_bars: list[Bar],
    costs: CostModel = ZERO_COST,
) -> SimulatedTrade | None:
    """Walk forward through bars (same symbol, after ``signal.ts``) and decide
    when the trade would have exited. Returns None if there are no follow-up bars.

    With ``costs=ZERO_COST`` (the default) fills are idealistic. Pass a non-zero
    :class:`CostModel` to model slippage and fees.
    """
    same_symbol = [b for b in subsequent_bars if b.symbol == signal.symbol and b.ts > signal.ts]
    if not same_symbol:
        return None

    stop = signal.stop_price
    target = signal.target_price

    for bar in same_symbol:
        if signal.side == "long":
            hit_stop = bar.low <= stop
            hit_target = bar.high >= target
        else:  # short
            hit_stop = bar.high >= stop
            hit_target = bar.low <= target

        if hit_stop:  # conservative: stop wins ties
            return _build_trade(
                signal=signal,
                qty=qty,
                exit_reason="stop",
                ideal_exit_price=stop,
                exit_ts_iso=bar.ts.isoformat(),
                exit_slips=True,
                costs=costs,
            )
        if hit_target:
            return _build_trade(
                signal=signal,
                qty=qty,
                exit_reason="target",
                ideal_exit_price=target,
                exit_ts_iso=bar.ts.isoformat(),
                exit_slips=False,  # a target is a limit order — no slippage
                costs=costs,
            )

    # No exit hit during the session → EOD flatten at the last bar's close.
    last = same_symbol[-1]
    return _build_trade(
        signal=signal,
        qty=qty,
        exit_reason="eod",
        ideal_exit_price=last.close,
        exit_ts_iso=last.ts.isoformat(),
        exit_slips=True,
        costs=costs,
    )
