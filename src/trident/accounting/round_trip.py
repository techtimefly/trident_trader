"""Pure round-trip P&L and wash-sale computation.

A *round trip* is one closed trade — an entry matched to its exit. This module
turns the raw entry/exit facts into realized P&L and a tax marker. It is pure:
no DB, no broker, no clock. The caller (``portfolio/tracking.py``) supplies the
facts and persists the :class:`~trident.persistence.models.LiveTrade` row.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

# IRS wash-sale window: a loss is disallowed if substantially identical stock is
# acquired within 30 days before OR after the sale.
WASH_SALE_WINDOW = timedelta(days=30)


@dataclass(frozen=True)
class RoundTrip:
    """A closed trade's realized economics — the computed fields of a LiveTrade."""

    symbol: str
    side: str  # long | short
    qty: int
    entry_ts: datetime
    entry_price: Decimal
    exit_ts: datetime
    exit_price: Decimal
    gross_pnl: Decimal  # before fees
    fees: Decimal
    net_pnl: Decimal  # gross minus fees
    r_multiple: Decimal | None  # net_pnl / initial risk; None if stop unknown
    holding_period_seconds: int


def compute_round_trip(
    *,
    symbol: str,
    side: str,
    qty: int,
    entry_ts: datetime,
    entry_price: Decimal,
    exit_ts: datetime,
    exit_price: Decimal,
    fees: Decimal = Decimal("0"),
    stop_price: Decimal | None = None,
) -> RoundTrip:
    """Compute realized P&L for one closed trade.

    ``qty`` is the (positive) share count. ``gross_pnl`` is direction-aware:
    a long profits when the exit is higher, a short when it is lower. When the
    original ``stop_price`` is known, ``r_multiple`` is net P&L over the initial
    dollar risk; otherwise it is None.
    """
    if side not in {"long", "short"}:
        raise ValueError(f"unknown side {side!r}")
    if qty <= 0:
        raise ValueError(f"qty must be > 0, got {qty}")

    shares = Decimal(qty)
    if side == "long":
        gross = (exit_price - entry_price) * shares
    else:  # short
        gross = (entry_price - exit_price) * shares
    net = gross - fees

    r_multiple: Decimal | None = None
    if stop_price is not None:
        risk = abs(entry_price - stop_price) * shares
        if risk > 0:
            r_multiple = net / risk

    holding = int((exit_ts - entry_ts).total_seconds())
    return RoundTrip(
        symbol=symbol,
        side=side,
        qty=qty,
        entry_ts=entry_ts,
        entry_price=entry_price,
        exit_ts=exit_ts,
        exit_price=exit_price,
        gross_pnl=gross,
        fees=fees,
        net_pnl=net,
        r_multiple=r_multiple,
        holding_period_seconds=holding,
    )


@dataclass(frozen=True)
class ExitCandidate:
    """A filled order that might be the one that closed a position."""

    side: str  # broker side: buy | sell
    filled_at: datetime
    avg_fill_price: Decimal


def pick_exit_order(
    candidates: list[ExitCandidate], entry_side: str, opened_at: datetime
) -> ExitCandidate | None:
    """The order that closed a position, or None.

    The closing order has the opposite broker side to the entry (a long is
    closed by a sell, a short by a buy) and filled at or after the position
    opened. The most recent such fill is the exit.
    """
    want = "sell" if entry_side == "long" else "buy"
    eligible = [c for c in candidates if c.side == want and c.filled_at >= opened_at]
    if not eligible:
        return None
    return max(eligible, key=lambda c: c.filled_at)


def is_wash_sale(
    *,
    symbol: str,
    exit_ts: datetime,
    net_pnl: Decimal,
    other_entries: list[tuple[str, datetime]],
) -> bool:
    """True if this closed trade is a wash sale.

    A wash sale requires (a) a realized loss and (b) a re-entry in the same
    symbol within 30 days before or after the closing sale. ``other_entries``
    is ``(symbol, entry_ts)`` for the account's other trades — the closing
    trade's own entry must not be included.
    """
    if net_pnl >= 0:
        return False
    return any(
        sym == symbol and abs(entry_ts - exit_ts) <= WASH_SALE_WINDOW
        for sym, entry_ts in other_entries
    )
