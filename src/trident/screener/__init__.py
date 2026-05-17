"""Mini stock screener — filter the US-equity universe by the few filters that
matter (price band, minimum average daily volume, recent % change).

The package splits cleanly into rings:

- :mod:`trident.screener.criteria` — pure value objects (``ScreenCriteria``,
  ``ScreenCandidate``, ``ScreenResult``). No I/O, money is ``Decimal``.
- :mod:`trident.screener.engine` — pure filter + rank functions. Exhaustively
  unit-tested with no network or database.
- :mod:`trident.screener.data` — the Alpaca-backed data layer; the only place
  that touches the network.
- :mod:`trident.screener.persistence` — writes a run + its rows to Postgres.

The pure core (``criteria`` + ``engine``) has zero dependencies on the data or
persistence modules, so the screening logic can be tested in isolation.
"""
from __future__ import annotations

from trident.screener.criteria import (
    ScreenCandidate,
    ScreenCriteria,
    ScreenResult,
)
from trident.screener.engine import passes, rank, screen

__all__ = [
    "ScreenCandidate",
    "ScreenCriteria",
    "ScreenResult",
    "passes",
    "rank",
    "screen",
]
