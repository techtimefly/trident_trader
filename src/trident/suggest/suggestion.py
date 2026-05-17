"""Value objects for the AI stock-suggestions feature — the inputs and outputs
of the advisory pre-market precheck.

Everything here is a frozen dataclass with no behaviour and no I/O. Money is
always ``Decimal``. These objects sit between the screener output (what the AI
reviews) and the persistence/dashboard layers (what the user reads).

The feature is **advisory only**: a :class:`StockSuggestion` is something the
user reads before deciding what to do. It is never wired into the risk gate,
the runners, the watchlist, or the order flow.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class PlanContext:
    """The user's daily-plan numbers, passed to the AI as context.

    All fields are optional — a ``None`` field means the user has not set that
    cap. The AI uses these to keep its suggestion list focused (e.g. not
    suggesting more names than the day-trade cap allows). Money is ``Decimal``.
    """

    budget_pct: Decimal | None = None
    """Capital budget as a percent of equity, or None when no cap is set."""

    max_day_trades: int | None = None
    """Rolling day-trade cap, or None when no cap is set."""

    def describe(self) -> str:
        """A short human-readable summary of the active plan numbers."""
        parts: list[str] = []
        if self.budget_pct is not None:
            parts.append(f"capital budget {self.budget_pct}% of equity")
        if self.max_day_trades is not None:
            parts.append(f"day-trade cap {self.max_day_trades}")
        return "; ".join(parts) if parts else "no daily plan set"


@dataclass(frozen=True, slots=True)
class StockSuggestion:
    """One stock the AI suggests the user watch, with its reasoning.

    ``rank`` is 1-based, best-first. ``confidence`` is the model's own
    low/medium/high label for how strong the suggestion is — it is a hint for
    the reader, not a number fed into any calculation.
    """

    symbol: str
    rationale: str
    """The model's plain-English reason for surfacing this symbol."""

    confidence: str = "medium"
    """The model's confidence label: ``low``, ``medium`` or ``high``."""

    rank: int = 0
    """1-based position in the suggestion list, best-first. 0 = unranked."""


# The set of confidence labels the model is asked to use. Anything outside
# this set is normalised to "medium" by the parser — the feature is advisory,
# so a surprising label degrades to a neutral default rather than erroring.
VALID_CONFIDENCE: frozenset[str] = frozenset({"low", "medium", "high"})


@dataclass(frozen=True, slots=True)
class SuggestionResult:
    """The outcome of an AI suggestion run: the suggestions plus run metadata.

    ``ok`` is False when no suggestion could be produced — most commonly
    because no API key is configured (the expected, common case in this
    environment). ``notice`` then carries a clear human-readable explanation.
    A not-ok result always has empty ``suggestions``.
    """

    suggestions: tuple[StockSuggestion, ...] = field(default_factory=tuple)
    ok: bool = True
    notice: str = ""
    """A human-readable explanation when ``ok`` is False (or an empty string)."""

    model: str = ""
    """The model id used for the run, or an empty string when none ran."""

    @property
    def count(self) -> int:
        """Number of suggestions produced."""
        return len(self.suggestions)

    @classmethod
    def degraded(cls, notice: str) -> SuggestionResult:
        """Build a not-ok result carrying ``notice`` and no suggestions.

        Used for every graceful-degradation path: missing API key, empty
        screener input, an unparseable model response, or an API error.
        """
        return cls(suggestions=(), ok=False, notice=notice, model="")
