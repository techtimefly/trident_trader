"""Single source of truth for the active screener filter set.

The screener's filters are a managed config: named presets in the
``screen_presets`` table, exactly one active. :func:`resolve_screen_criteria`
reads the active preset and falls back to the module's static default if no
preset is active or the DB is unavailable — so it never fails to produce a
criteria.

Mirrors :mod:`trident.watchlist` — the watchlist is to the runner what a screen
preset is to the screener.
"""
from __future__ import annotations

import logging

from trident.screener.criteria import ScreenCriteria

_log = logging.getLogger(__name__)

# The lookback window (trading days) the default screen measures average
# volume and % change over.
DEFAULT_LOOKBACK_DAYS: int = 20

# The screen used when no preset has been activated (and the DB-down fallback).
# Intentionally minimal — just a liquidity floor, no opinionated price/sector
# bound. Create and activate a preset in the dashboard to change what the
# screener actually runs.
DEFAULT_CRITERIA: ScreenCriteria = ScreenCriteria(min_avg_volume=500_000)


def resolve_screen_criteria() -> tuple[ScreenCriteria, int]:
    """Return the active screen preset's ``(criteria, lookback_days)``.

    Reads the most-recently-activated row from the ``screen_presets`` table.
    Falls back to (:data:`DEFAULT_CRITERIA`, :data:`DEFAULT_LOOKBACK_DAYS`) when
    no preset is active or the DB is unavailable. Never raises.
    """
    try:
        from trident.persistence.screen_presets_store import get_active_preset

        preset = get_active_preset()
        if preset is not None:
            return preset.criteria, preset.lookback_days
    except Exception:
        _log.warning(
            "resolve_screen_criteria: DB lookup failed, using static fallback",
            exc_info=True,
        )
    return DEFAULT_CRITERIA, DEFAULT_LOOKBACK_DAYS
