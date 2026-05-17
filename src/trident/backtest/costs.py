"""Trade-cost model for the honest backtest harness.

The idealistic fill simulator (``simulator.py``) fills at exact prices with no
costs. This module supplies the two things that make a backtest honest:
slippage and fees.

Slippage is *synthetic*. Alpaca's free IEX feed provides OHLCV bars only — no
bid/ask quotes — so there is no spread to sample. We model slippage as a fixed
number of basis points of the fill price, always moving the price against the
trader. This is an assumption, not a measurement; tune ``slippage_bps`` to taste.

Fees: Alpaca charges $0 commission on US equities, but the SEC fee and FINRA TAF
apply to *sells*. The model carries broker commission + SEC + TAF so it stays
honest and broker-agnostic; every field defaults to zero, so ``ZERO_COST`` is a
genuine no-op and the simulator's idealistic behaviour is preserved.

All money/price math is ``Decimal``, never ``float``.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

_BPS = Decimal("10000")
_CENT = Decimal("0.01")


@dataclass(frozen=True)
class CostModel:
    """Per-trade cost parameters. All-zero defaults make the model a no-op."""

    slippage_bps: Decimal = Decimal("0")
    """Slippage in basis points of the fill price, applied to market-order legs."""

    fee_per_share: Decimal = Decimal("0")
    """Broker commission per share, per order leg (Alpaca US equities = 0)."""

    min_fee: Decimal = Decimal("0")
    """Per-order commission floor."""

    sec_fee_rate: Decimal = Decimal("0")
    """SEC fee as a fraction of sell-leg notional (sells only)."""

    taf_per_share: Decimal = Decimal("0")
    """FINRA TAF per share, charged on sells only."""


ZERO_COST = CostModel()
"""The idealistic model — no slippage, no fees. Keeps ``simulate_trade`` backward
compatible: passing ``ZERO_COST`` reproduces the pre-cost behaviour exactly."""


def _is_buy(side: str, action: str) -> bool:
    """A long entry or a short exit buys; a long exit or short entry sells."""
    if action == "enter":
        return side == "long"
    return side == "short"


def round_cents(price: Decimal) -> Decimal:
    """Quantize a price to whole cents (round half up) — real fills are in pennies."""
    return price.quantize(_CENT, rounding=ROUND_HALF_UP)


def apply_slippage(price: Decimal, side: str, action: str, costs: CostModel) -> Decimal:
    """Move ``price`` against the trader by ``slippage_bps`` and round to the cent.

    ``action`` is ``"enter"`` or ``"exit"``. Buys (long entry, short exit) fill
    higher; sells (long exit, short entry) fill lower. With ``slippage_bps == 0``
    the price is returned unchanged — exactly, with no rounding — so a zero-cost
    model is a true no-op.
    """
    if costs.slippage_bps == 0:
        return price
    delta = price * costs.slippage_bps / _BPS
    slipped = price + delta if _is_buy(side, action) else price - delta
    return round_cents(slipped)


def per_share_fee(qty: int, costs: CostModel) -> Decimal:
    """Broker commission for one order leg: ``max(fee_per_share * qty, min_fee)``.

    Returns 0 when the model has neither a per-share commission nor a floor.
    """
    if costs.fee_per_share == 0 and costs.min_fee == 0:
        return Decimal("0")
    return max(costs.fee_per_share * Decimal(qty), costs.min_fee)


def regulatory_fee(notional: Decimal, qty: int, is_sell: bool, costs: CostModel) -> Decimal:
    """SEC fee + FINRA TAF for one leg. Charged on sells only; 0 for buys.

    ``notional`` is the dollar value of the leg (fill price * qty).
    """
    if not is_sell:
        return Decimal("0")
    return notional * costs.sec_fee_rate + costs.taf_per_share * Decimal(qty)
