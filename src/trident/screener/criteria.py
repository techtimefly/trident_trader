"""Value objects for the stock screener — the screen's inputs and outputs.

All three are frozen dataclasses with no behaviour and no I/O. Money is always
``Decimal``; a ``None`` (or empty-tuple) bound on :class:`ScreenCriteria` means
"no bound on that side", so ``ScreenCriteria()`` matches every candidate.

The screener exposes the filters that matter for a personal day-trading
watchlist:

- **price band** — ``min_price`` / ``max_price``. The max bound is the one that
  finds low-priced / sub-$1 names (``max_price = Decimal("1.00")``).
- **minimum average daily volume** — liquidity floor; thin names are untradeable.
- **recent % change band** — ``min_change_pct`` / ``max_change_pct`` over the
  lookback window.
- **market-cap band** — ``min_market_cap`` / ``max_market_cap``, in dollars.
- **sector / exchange allow-lists** — ``sectors`` / ``exchanges``.

Market cap, sector, and exchange are sourced from Financial Modeling Prep (see
:mod:`trident.screener.fmp`); a candidate built without that metadata carries
``None`` for them and so fails any such bound (reject-on-doubt).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class ScreenCriteria:
    """The screen's filter bounds. ``None`` on any field means 'no bound'.

    All bounds are inclusive. An empty ``ScreenCriteria()`` passes everything.
    Validation (e.g. min greater than max) is the caller's job — the pure
    :func:`trident.screener.engine.passes` simply applies whatever it is given.
    """

    min_price: Decimal | None = None
    """Inclusive lower bound on the latest price, in dollars."""

    max_price: Decimal | None = None
    """Inclusive upper bound on the latest price. Set this to find cheap stocks
    (e.g. ``Decimal("1.00")`` for sub-$1 names)."""

    min_avg_volume: int | None = None
    """Inclusive lower bound on average daily share volume over the lookback."""

    min_change_pct: Decimal | None = None
    """Inclusive lower bound on the recent % change over the lookback window."""

    max_change_pct: Decimal | None = None
    """Inclusive upper bound on the recent % change over the lookback window."""

    min_market_cap: int | None = None
    """Inclusive lower bound on market capitalization, in dollars."""

    max_market_cap: int | None = None
    """Inclusive upper bound on market capitalization, in dollars."""

    sectors: tuple[str, ...] = ()
    """Allow-list of sector names; an empty tuple means no sector bound."""

    exchanges: tuple[str, ...] = ()
    """Allow-list of exchange short codes (NASDAQ/NYSE/AMEX); empty = no bound."""

    def describe(self) -> str:
        """A short human-readable summary of the active bounds."""
        parts: list[str] = []
        if self.min_price is not None:
            parts.append(f"price >= ${self.min_price}")
        if self.max_price is not None:
            parts.append(f"price <= ${self.max_price}")
        if self.min_avg_volume is not None:
            parts.append(f"avg vol >= {self.min_avg_volume:,}")
        if self.min_change_pct is not None:
            parts.append(f"change >= {self.min_change_pct}%")
        if self.max_change_pct is not None:
            parts.append(f"change <= {self.max_change_pct}%")
        if self.min_market_cap is not None:
            parts.append(f"mkt cap >= ${self.min_market_cap:,}")
        if self.max_market_cap is not None:
            parts.append(f"mkt cap <= ${self.max_market_cap:,}")
        if self.sectors:
            parts.append(f"sector in {{{', '.join(self.sectors)}}}")
        if self.exchanges:
            parts.append(f"exchange in {{{', '.join(self.exchanges)}}}")
        return "  ".join(parts) if parts else "no filters (matches everything)"


@dataclass(frozen=True, slots=True)
class ScreenCandidate:
    """One symbol's market facts, the raw input to a screen.

    Produced by the data layer (one per symbol in the scanned universe) and
    consumed by the pure engine. ``change_pct`` is the percentage move over the
    same lookback window the average volume was measured on.
    """

    symbol: str
    price: Decimal
    """Latest available daily close, in dollars."""

    avg_volume: int
    """Average daily share volume over the lookback window."""

    change_pct: Decimal
    """Percentage change of the latest close vs. the first close in the window."""

    market_cap: int | None = None
    """Market capitalization in dollars, from FMP. None when unavailable."""

    sector: str | None = None
    """Sector name, from FMP. None when unavailable."""

    exchange: str | None = None
    """Listing exchange short code (NASDAQ/NYSE/AMEX), from FMP. None when
    unavailable."""


@dataclass(frozen=True, slots=True)
class ScreenResult:
    """The outcome of running a screen: the criteria, ranked matches, and counts.

    ``matches`` is ranked best-first (highest average volume first — the most
    liquid, most tradeable names lead). ``scanned`` is how many candidates were
    fed in, so the dashboard can show "12 of 200 matched".
    """

    criteria: ScreenCriteria
    matches: tuple[ScreenCandidate, ...] = field(default_factory=tuple)
    scanned: int = 0

    @property
    def matched(self) -> int:
        """Number of candidates that passed every filter."""
        return len(self.matches)
