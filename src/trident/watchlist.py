"""Single source of truth for the runner watchlist.

The watchlist was a ``WATCHLIST`` constant duplicated verbatim across five
scripts (shadow_run, paper_run, replay, backtest, backfill_daily). This module
owns it. ``resolve_watchlist()`` is the seam a future DB-backed, dashboard-
approved dynamic watchlist hooks into — today it simply returns the constant,
so callers can adopt it now and gain the dynamic behaviour later for free.
"""
from __future__ import annotations

# The six liquid large-caps the ORB strategy was validated on.
WATCHLIST: list[str] = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMD"]


def resolve_watchlist() -> list[str]:
    """Return today's active watchlist.

    Currently a copy of the static :data:`WATCHLIST`. A later phase reroutes
    this to a DB-backed, dashboard-approved list while keeping this signature,
    so callers never change. A fresh list is returned each call so a caller
    mutating the result cannot corrupt the module constant.
    """
    return list(WATCHLIST)
