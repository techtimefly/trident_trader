"""The pure screening engine: filter and rank candidates against criteria.

Every function here is pure — no I/O, no network, no database, no clock — so the
screening logic is exhaustively unit-testable in isolation. The data layer
(``trident.screener.data``) supplies the candidates; this module only decides
which pass and in what order.
"""
from __future__ import annotations

from collections.abc import Iterable

from trident.screener.criteria import ScreenCandidate, ScreenCriteria, ScreenResult


def passes(candidate: ScreenCandidate, criteria: ScreenCriteria) -> bool:
    """True iff ``candidate`` satisfies every active bound in ``criteria``.

    All numeric bounds are inclusive; a ``None`` bound (or an empty ``sectors``
    / ``exchanges`` tuple) is skipped. With an empty ``ScreenCriteria()`` every
    candidate passes. Short-circuits on the first failed bound.

    Market-cap, sector, and exchange are FMP-sourced metadata: when a bound on
    one of them is active but the candidate carries no such value, the
    candidate is rejected (reject-on-doubt — we cannot prove it passes).
    """
    if criteria.min_price is not None and candidate.price < criteria.min_price:
        return False
    if criteria.max_price is not None and candidate.price > criteria.max_price:
        return False
    if criteria.min_avg_volume is not None and candidate.avg_volume < criteria.min_avg_volume:
        return False
    if (
        criteria.min_change_pct is not None
        and candidate.change_pct < criteria.min_change_pct
    ):
        return False
    if (
        criteria.max_change_pct is not None
        and candidate.change_pct > criteria.max_change_pct
    ):
        return False
    if criteria.min_market_cap is not None and (
        candidate.market_cap is None or candidate.market_cap < criteria.min_market_cap
    ):
        return False
    if criteria.max_market_cap is not None and (
        candidate.market_cap is None or candidate.market_cap > criteria.max_market_cap
    ):
        return False
    if criteria.sectors and candidate.sector not in criteria.sectors:
        return False
    return not (criteria.exchanges and candidate.exchange not in criteria.exchanges)


def rank(candidates: Iterable[ScreenCandidate]) -> tuple[ScreenCandidate, ...]:
    """Order candidates best-first: most liquid (highest avg volume) leads.

    Ties on volume break on symbol (ascending) for a stable, deterministic
    order — important so a re-run with the same data produces the same table.
    """
    return tuple(
        sorted(candidates, key=lambda c: (-c.avg_volume, c.symbol))
    )


def screen(
    candidates: Iterable[ScreenCandidate], criteria: ScreenCriteria
) -> ScreenResult:
    """Run a full screen: filter ``candidates`` by ``criteria``, then rank them.

    Returns a :class:`ScreenResult` carrying the criteria, the ranked matches,
    and the count of candidates scanned. Pure — the same inputs always give the
    same result.
    """
    pool = list(candidates)
    matches = rank(c for c in pool if passes(c, criteria))
    return ScreenResult(criteria=criteria, matches=matches, scanned=len(pool))
