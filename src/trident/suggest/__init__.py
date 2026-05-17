"""AI stock-suggestions — an advisory pre-market precheck.

Claude (the Anthropic API) reviews the latest stock-screener output and
suggests a small, focused set of stocks for the user to watch, with reasoning.
This is Phase 3 of the app roadmap.

**Advisory only.** The output is a suggestion list the user reads. It is
deliberately NOT wired into the risk gate, the runners, the watchlist, or the
order flow — the human stays in the loop. The package produces suggestions and
nothing more.

The package splits cleanly into rings:

- :mod:`trident.suggest.suggestion` — pure value objects (``StockSuggestion``,
  ``PlanContext``, ``SuggestionResult``). No I/O, money is ``Decimal``.
- :mod:`trident.suggest.prompt` — pure prompt-building and response-parsing
  functions. Exhaustively unit-tested with no network.
- :mod:`trident.suggest.client` — the Anthropic-API client layer; the only
  place that touches the network. Degrades gracefully when no key is set.
- :mod:`trident.suggest.persistence` — writes a run + its suggestions to
  Postgres so the dashboard can show them.

The pure core (``suggestion`` + ``prompt``) has zero dependencies on the
client or persistence modules, so the logic can be tested in isolation.
"""
from __future__ import annotations

from trident.suggest.client import DEFAULT_MODEL, suggest_stocks
from trident.suggest.prompt import (
    build_user_prompt,
    parse_suggestions,
    system_prompt,
)
from trident.suggest.suggestion import (
    PlanContext,
    StockSuggestion,
    SuggestionResult,
)

__all__ = [
    "DEFAULT_MODEL",
    "PlanContext",
    "StockSuggestion",
    "SuggestionResult",
    "build_user_prompt",
    "parse_suggestions",
    "suggest_stocks",
    "system_prompt",
]
