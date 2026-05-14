from __future__ import annotations

from decimal import ROUND_DOWN, Decimal


def position_size(
    account_equity: Decimal,
    risk_per_trade_pct: Decimal,
    entry_price: Decimal,
    stop_price: Decimal,
) -> int:
    """Return the integer share count such that hitting the stop loses ~risk_per_trade_pct of equity.

    Floors fractional shares to whole. Returns 0 if any input is non-positive or if the
    stop is on the wrong side of the entry (caller should treat 0 as "do not trade").
    """
    if account_equity <= 0 or risk_per_trade_pct <= 0:
        return 0
    if entry_price <= 0 or stop_price <= 0:
        return 0

    risk_per_share = abs(entry_price - stop_price)
    if risk_per_share == 0:
        return 0

    risk_dollars = account_equity * (risk_per_trade_pct / Decimal("100"))
    shares = risk_dollars / risk_per_share
    shares_int = int(shares.quantize(Decimal("1"), rounding=ROUND_DOWN))
    return max(shares_int, 0)


def position_notional(shares: int, entry_price: Decimal) -> Decimal:
    return Decimal(shares) * entry_price


def position_fits_buying_power(
    shares: int,
    entry_price: Decimal,
    buying_power: Decimal,
    safety_buffer_pct: Decimal = Decimal("5"),
) -> bool:
    """Return True if the order plus a buffer fits inside buying_power."""
    notional = position_notional(shares, entry_price)
    buffer = notional * (safety_buffer_pct / Decimal("100"))
    return (notional + buffer) <= buying_power
