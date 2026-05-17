"""Pure logic for the AI stock-suggestions feature: build the prompt from
screener results + daily-plan context, and parse the model's JSON response
into :class:`~trident.suggest.suggestion.StockSuggestion` objects.

Every function here is pure — no network, no database, no clock — so the
prompt-building and response-parsing logic is exhaustively unit-testable in
isolation. The network lives in :mod:`trident.suggest.client`.

The prompt is split into two halves so the API client can cache the stable
half (the system instructions) and only pay full price for the volatile half
(the screener table, which changes every run):

- :func:`system_prompt` — frozen instructions. Never changes between runs, so
  it is a clean prompt-cache prefix.
- :func:`build_user_prompt` — the screener candidates + plan context. Changes
  every run.
"""
from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from decimal import Decimal
from typing import Any

from trident.screener.criteria import ScreenCandidate
from trident.suggest.suggestion import (
    VALID_CONFIDENCE,
    PlanContext,
    StockSuggestion,
)

# How many suggestions we ask the model for, at most. A pre-market precheck is
# meant to be focused — a short list the user can actually act on, not the
# whole screener table re-ranked.
DEFAULT_MAX_SUGGESTIONS = 5

# The frozen system instructions. Kept as a module constant (not interpolated)
# so it is byte-identical between runs and works as a prompt-cache prefix.
_SYSTEM_PROMPT = """\
You are a pre-market precheck assistant for a single-user personal day-trading \
tool. The user runs a stock screener that ranks symbols by price, average \
volume, and recent percentage change. Your job is to review that screener \
output and suggest a small, focused set of stocks for the user to WATCH today.

Your output is advisory only. The user reads your suggestions and decides for \
themselves — nothing you say is executed automatically. Do not give financial \
advice, price targets, or position sizes. Do not tell the user to buy or sell. \
Frame everything as "worth watching because ...".

Rules:
- Suggest at most the requested number of symbols. Fewer is fine.
- Only suggest symbols that appear in the screener candidates provided.
- Prefer liquid names (higher average volume) — thin stocks are hard to trade.
- Keep each rationale to one or two plain sentences grounded in the screener \
facts (price, volume, % change). No hype.
- If the screener output is weak (nothing stands out), it is correct to return \
an empty list.

Respond with ONLY a JSON object, no prose around it, of this exact shape:
{"suggestions": [{"symbol": "AAA", "rationale": "...", "confidence": "low|medium|high"}]}
The list is ordered best-first. confidence is your own qualitative label."""


def system_prompt() -> str:
    """Return the frozen system instructions for the suggestion model.

    Byte-identical across runs — safe to use as a prompt-cache prefix.
    """
    return _SYSTEM_PROMPT


def _decimal_str(d: Decimal) -> str:
    """A plain Decimal string — no scientific notation, deterministic."""
    return format(d, "f")


def _candidate_payload(candidate: ScreenCandidate) -> dict[str, Any]:
    """One screener candidate as a plain JSON-serialisable dict.

    Decimals are stringified so no float ever enters the prompt and the
    serialisation is deterministic (stable bytes for a stable input).
    """
    return {
        "symbol": candidate.symbol,
        "price": _decimal_str(candidate.price),
        "avg_volume": candidate.avg_volume,
        "change_pct": _decimal_str(candidate.change_pct),
    }


def build_user_prompt(
    candidates: Iterable[ScreenCandidate],
    plan: PlanContext,
    *,
    max_suggestions: int = DEFAULT_MAX_SUGGESTIONS,
) -> str:
    """Build the volatile half of the prompt — screener table + plan context.

    ``candidates`` is the ranked screener output to review. ``plan`` is the
    user's daily-plan numbers (used only to keep the suggestion count sane).
    ``max_suggestions`` caps how many symbols the model is asked for.

    Pure: the same inputs always produce the same string. The screener table
    is emitted as deterministic JSON so a re-run with the same data builds the
    same prompt (which also keeps prompt caching honest).
    """
    pool = list(candidates)
    table = [_candidate_payload(c) for c in pool]
    body = {
        "max_suggestions": max(0, max_suggestions),
        "daily_plan": plan.describe(),
        "screener_candidates": table,
    }
    return (
        "Review today's stock screener output and suggest which symbols are "
        "worth watching. The candidates below are already ranked best-first "
        "by the screener.\n\n"
        f"{json.dumps(body, indent=2, sort_keys=True)}\n\n"
        "Return the JSON object described in your instructions."
    )


def has_candidates(candidates: Sequence[ScreenCandidate]) -> bool:
    """True iff there is at least one screener candidate to review.

    The caller (the client / CLI) uses this to short-circuit: with no
    candidates there is nothing for the AI to do, so no API call is made.
    """
    return len(candidates) > 0


def _normalise_confidence(raw: Any) -> str:
    """Coerce a model-supplied confidence value to a valid label.

    Anything outside :data:`~trident.suggest.suggestion.VALID_CONFIDENCE`
    degrades to ``"medium"`` — the feature is advisory, so a surprising label
    is a neutral default rather than an error.
    """
    if isinstance(raw, str):
        lowered = raw.strip().lower()
        if lowered in VALID_CONFIDENCE:
            return lowered
    return "medium"


def parse_suggestions(
    raw_text: str,
    *,
    allowed_symbols: Iterable[str] | None = None,
    max_suggestions: int = DEFAULT_MAX_SUGGESTIONS,
) -> tuple[StockSuggestion, ...]:
    """Parse the model's JSON response into ranked :class:`StockSuggestion`s.

    ``raw_text`` is the model's reply. It is expected to be a JSON object of
    the shape ``{"suggestions": [{"symbol", "rationale", "confidence"}]}`` —
    the parser tolerates surrounding prose by extracting the first ``{...}``
    span.

    ``allowed_symbols``, when given, restricts the result to symbols the
    screener actually surfaced — a guard against the model inventing tickers.
    ``max_suggestions`` truncates an over-long list.

    Pure and total: any malformed input yields an empty tuple rather than
    raising. Returned suggestions are re-ranked 1..N in list order.
    """
    obj = _extract_json_object(raw_text)
    if obj is None:
        return ()
    items = obj.get("suggestions")
    if not isinstance(items, list):
        return ()

    cap = max(0, max_suggestions)
    allowed = {s.upper() for s in allowed_symbols} if allowed_symbols is not None else None
    out: list[StockSuggestion] = []
    seen: set[str] = set()
    for item in items:
        if len(out) >= cap:
            break
        suggestion = _suggestion_from_item(item, allowed, seen)
        if suggestion is not None:
            seen.add(suggestion.symbol)
            out.append(suggestion)
    # Re-rank 1..N in the (already best-first) list order.
    return tuple(
        StockSuggestion(
            symbol=s.symbol,
            rationale=s.rationale,
            confidence=s.confidence,
            rank=idx,
        )
        for idx, s in enumerate(out, start=1)
    )


def _suggestion_from_item(
    item: Any, allowed: set[str] | None, seen: set[str]
) -> StockSuggestion | None:
    """Build one StockSuggestion from a raw list item, or None if unusable.

    Drops items that are not dicts, have no symbol, repeat a symbol already
    seen, or name a symbol outside the allowed set.
    """
    if not isinstance(item, dict):
        return None
    raw_symbol = item.get("symbol")
    if not isinstance(raw_symbol, str) or not raw_symbol.strip():
        return None
    symbol = raw_symbol.strip().upper()
    if symbol in seen:
        return None
    if allowed is not None and symbol not in allowed:
        return None
    raw_rationale = item.get("rationale")
    rationale = raw_rationale.strip() if isinstance(raw_rationale, str) else ""
    return StockSuggestion(
        symbol=symbol,
        rationale=rationale,
        confidence=_normalise_confidence(item.get("confidence")),
        rank=0,
    )


def _extract_json_object(text: str) -> dict[str, Any] | None:
    """Pull the first top-level JSON object out of ``text``, or None.

    Tries a straight parse first, then falls back to the substring between the
    first ``{`` and the last ``}`` — which tolerates a model that wraps its
    JSON in prose or a ```` ```json ```` fence. Returns None for anything that
    will not parse into a dict.
    """
    stripped = text.strip()
    candidates = [stripped]
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidates.append(stripped[start : end + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return None
