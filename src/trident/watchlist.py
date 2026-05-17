"""Single source of truth for the runner watchlist.

The watchlist was a ``WATCHLIST`` constant duplicated verbatim across five
scripts (shadow_run, paper_run, replay, backtest, backfill_daily). This module
owns it. ``resolve_watchlist()`` reads the most-recently-activated row from the
``watchlists`` DB table and falls back to the static constant if no active row
exists or if the DB is unavailable — so it never resolves to an empty list.
"""
from __future__ import annotations

import logging

_log = logging.getLogger(__name__)

# The six liquid large-caps the ORB strategy was validated on.
WATCHLIST: list[str] = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMD"]


def resolve_watchlist() -> list[str]:
    """Return today's active watchlist.

    Queries the DB for the most-recently-activated watchlist row. Falls back to
    the static :data:`WATCHLIST` constant if no active row exists or if the DB
    is unavailable. Never returns an empty list — the static constant is the
    last-resort guarantee.
    """
    try:
        from trident.persistence.watchlist_store import get_active_watchlist

        record = get_active_watchlist()
        if record is not None and record.symbols:
            return list(record.symbols)
    except Exception:
        _log.warning(
            "resolve_watchlist: DB lookup failed, using static fallback",
            exc_info=True,
        )
    return list(WATCHLIST)
