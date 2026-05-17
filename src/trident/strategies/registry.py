"""Strategy registry — look up a strategy implementation by name.

Runners and the backtest engine select a strategy by name (a ``--strategy`` CLI
arg) instead of importing one concrete class. The registry maps a name to a
*builder* callable rather than an instance: strategies hold mutable per-day
state, so every run must get a fresh object.

A new strategy becomes selectable by adding one ``register(...)`` line at the
bottom of this module — nothing else in the codebase needs to change.
"""
from __future__ import annotations

from collections.abc import Callable

from trident.strategies.base import Strategy
from trident.strategies.orb import OpeningRangeBreakout
from trident.strategies.vwap_reversion import VWAPReversion

# A builder takes the watchlist symbols and returns a fresh Strategy instance.
StrategyBuilder = Callable[[list[str]], Strategy]

_REGISTRY: dict[str, StrategyBuilder] = {}


def register(name: str, builder: StrategyBuilder) -> None:
    """Register ``builder`` under ``name``. Raises on a duplicate name so an
    accidental double-registration fails loudly rather than shadowing silently.
    """
    if name in _REGISTRY:
        raise ValueError(f"Strategy {name!r} is already registered.")
    _REGISTRY[name] = builder


def available_strategies() -> list[str]:
    """Return the registered strategy names, sorted."""
    return sorted(_REGISTRY)


def build_strategy(name: str, symbols: list[str]) -> Strategy:
    """Build a fresh strategy instance by name.

    Raises ``ValueError`` (listing the available names) on an unknown name — a
    CLI typo should fail loudly, not silently fall back to a default.
    """
    try:
        builder = _REGISTRY[name]
    except KeyError:
        raise ValueError(
            f"Unknown strategy {name!r}. Available: {', '.join(available_strategies())}"
        ) from None
    return builder(symbols)


# Built-in strategies. Keyed by the strategy's own ``name`` so the registry key
# can never drift from what the strategy reports about itself.
register(OpeningRangeBreakout.name, lambda symbols: OpeningRangeBreakout(symbols=symbols))
register(VWAPReversion.name, lambda symbols: VWAPReversion(symbols=symbols))
