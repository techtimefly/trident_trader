"""Exhaustive unit tests for the pure AI-suggestions core.

Covers the value objects (``suggestion``), the pure prompt-building and
response-parsing logic (``prompt``), and the graceful no-API-key path of the
client layer. No network — the client tests stub ``get_settings`` so the
missing-key branch is exercised without ever importing the Anthropic SDK.
"""
from __future__ import annotations

import json
from decimal import Decimal

from trident.screener.criteria import ScreenCandidate
from trident.suggest import client as suggest_client
from trident.suggest.client import suggest_stocks
from trident.suggest.prompt import (
    DEFAULT_MAX_SUGGESTIONS,
    build_user_prompt,
    has_candidates,
    parse_suggestions,
    system_prompt,
)
from trident.suggest.suggestion import (
    VALID_CONFIDENCE,
    PlanContext,
    StockSuggestion,
    SuggestionResult,
)


def _cand(
    symbol: str = "AAA",
    price: str = "10.00",
    avg_volume: int = 1_000_000,
    change_pct: str = "1.50",
) -> ScreenCandidate:
    return ScreenCandidate(
        symbol=symbol,
        price=Decimal(price),
        avg_volume=avg_volume,
        change_pct=Decimal(change_pct),
    )


# --------------------------------------------------------------------------
# PlanContext
# --------------------------------------------------------------------------


def test_empty_plan_describes_as_no_plan() -> None:
    assert PlanContext().describe() == "no daily plan set"


def test_plan_describes_budget_only() -> None:
    text = PlanContext(budget_pct=Decimal("25")).describe()
    assert "25" in text
    assert "day-trade" not in text


def test_plan_describes_both_knobs() -> None:
    text = PlanContext(budget_pct=Decimal("10.5"), max_day_trades=3).describe()
    assert "10.5" in text
    assert "3" in text


# --------------------------------------------------------------------------
# StockSuggestion / SuggestionResult
# --------------------------------------------------------------------------


def test_stock_suggestion_defaults() -> None:
    s = StockSuggestion(symbol="AAA", rationale="liquid and moving")
    assert s.confidence == "medium"
    assert s.rank == 0


def test_suggestion_is_frozen() -> None:
    s = StockSuggestion(symbol="AAA", rationale="x")
    try:
        s.symbol = "BBB"  # type: ignore[misc]
    except AttributeError:
        return
    raise AssertionError("StockSuggestion should be frozen")


def test_suggestion_result_count() -> None:
    result = SuggestionResult(
        suggestions=(
            StockSuggestion(symbol="AAA", rationale="x", rank=1),
            StockSuggestion(symbol="BBB", rationale="y", rank=2),
        )
    )
    assert result.count == 2
    assert result.ok is True


def test_suggestion_result_degraded() -> None:
    result = SuggestionResult.degraded("no key")
    assert result.ok is False
    assert result.notice == "no key"
    assert result.count == 0
    assert result.model == ""


def test_valid_confidence_set() -> None:
    assert frozenset({"low", "medium", "high"}) == VALID_CONFIDENCE


# --------------------------------------------------------------------------
# system_prompt
# --------------------------------------------------------------------------


def test_system_prompt_is_stable() -> None:
    # Byte-identical between calls — required for it to be a cache prefix.
    assert system_prompt() == system_prompt()


def test_system_prompt_states_advisory_only() -> None:
    text = system_prompt().lower()
    assert "advisory" in text
    assert "json" in text


# --------------------------------------------------------------------------
# build_user_prompt
# --------------------------------------------------------------------------


def test_build_user_prompt_includes_candidate_facts() -> None:
    prompt = build_user_prompt([_cand("NVDA", price="120.50")], PlanContext())
    assert "NVDA" in prompt
    assert "120.50" in prompt


def test_build_user_prompt_is_deterministic() -> None:
    cands = [_cand("AAA"), _cand("BBB")]
    assert build_user_prompt(cands, PlanContext()) == build_user_prompt(
        cands, PlanContext()
    )


def test_build_user_prompt_carries_plan_and_max() -> None:
    plan = PlanContext(budget_pct=Decimal("20"), max_day_trades=2)
    prompt = build_user_prompt([_cand()], plan, max_suggestions=3)
    assert "20" in prompt
    assert '"max_suggestions": 3' in prompt


def test_build_user_prompt_no_float_in_payload() -> None:
    # Decimals must be stringified — no bare float in the JSON.
    prompt = build_user_prompt([_cand(price="3.30", change_pct="-2.20")], PlanContext())
    # The screener table is the JSON object embedded in the prompt.
    start = prompt.find("{")
    end = prompt.rfind("}")
    payload = json.loads(prompt[start : end + 1])
    row = payload["screener_candidates"][0]
    assert row["price"] == "3.30"
    assert row["change_pct"] == "-2.20"


def test_build_user_prompt_empty_candidates() -> None:
    prompt = build_user_prompt([], PlanContext())
    start = prompt.find("{")
    end = prompt.rfind("}")
    payload = json.loads(prompt[start : end + 1])
    assert payload["screener_candidates"] == []


def test_build_user_prompt_clamps_negative_max() -> None:
    prompt = build_user_prompt([_cand()], PlanContext(), max_suggestions=-3)
    assert '"max_suggestions": 0' in prompt


# --------------------------------------------------------------------------
# has_candidates
# --------------------------------------------------------------------------


def test_has_candidates_true_and_false() -> None:
    assert has_candidates([_cand()]) is True
    assert has_candidates([]) is False


# --------------------------------------------------------------------------
# parse_suggestions — happy path
# --------------------------------------------------------------------------


def _resp(*items: dict[str, str]) -> str:
    return json.dumps({"suggestions": list(items)})


def test_parse_basic_response() -> None:
    raw = _resp(
        {"symbol": "AAA", "rationale": "liquid", "confidence": "high"},
        {"symbol": "BBB", "rationale": "moving", "confidence": "low"},
    )
    suggestions = parse_suggestions(raw)
    assert [s.symbol for s in suggestions] == ["AAA", "BBB"]
    assert suggestions[0].confidence == "high"
    assert suggestions[1].confidence == "low"


def test_parse_assigns_1_based_ranks() -> None:
    raw = _resp(
        {"symbol": "AAA", "rationale": "x"},
        {"symbol": "BBB", "rationale": "y"},
        {"symbol": "CCC", "rationale": "z"},
    )
    suggestions = parse_suggestions(raw)
    assert [s.rank for s in suggestions] == [1, 2, 3]


def test_parse_uppercases_and_strips_symbol() -> None:
    raw = _resp({"symbol": "  nvda ", "rationale": "x"})
    assert parse_suggestions(raw)[0].symbol == "NVDA"


def test_parse_tolerates_prose_wrapping_json() -> None:
    raw = "Here is my analysis:\n```json\n" + _resp({"symbol": "AAA", "rationale": "x"}) + "\n```"
    assert parse_suggestions(raw)[0].symbol == "AAA"


# --------------------------------------------------------------------------
# parse_suggestions — confidence normalisation
# --------------------------------------------------------------------------


def test_parse_unknown_confidence_normalises_to_medium() -> None:
    raw = _resp({"symbol": "AAA", "rationale": "x", "confidence": "extreme"})
    assert parse_suggestions(raw)[0].confidence == "medium"


def test_parse_missing_confidence_defaults_to_medium() -> None:
    raw = _resp({"symbol": "AAA", "rationale": "x"})
    assert parse_suggestions(raw)[0].confidence == "medium"


def test_parse_confidence_case_insensitive() -> None:
    raw = _resp({"symbol": "AAA", "rationale": "x", "confidence": "HIGH"})
    assert parse_suggestions(raw)[0].confidence == "high"


# --------------------------------------------------------------------------
# parse_suggestions — filtering & guards
# --------------------------------------------------------------------------


def test_parse_restricts_to_allowed_symbols() -> None:
    raw = _resp(
        {"symbol": "AAA", "rationale": "x"},
        {"symbol": "FAKE", "rationale": "invented ticker"},
    )
    suggestions = parse_suggestions(raw, allowed_symbols=["AAA", "BBB"])
    assert [s.symbol for s in suggestions] == ["AAA"]


def test_parse_dedupes_repeated_symbol() -> None:
    raw = _resp(
        {"symbol": "AAA", "rationale": "first"},
        {"symbol": "aaa", "rationale": "dup"},
    )
    suggestions = parse_suggestions(raw)
    assert len(suggestions) == 1
    assert suggestions[0].rationale == "first"


def test_parse_truncates_to_max_suggestions() -> None:
    raw = _resp(*[{"symbol": f"S{i}", "rationale": "x"} for i in range(10)])
    suggestions = parse_suggestions(raw, max_suggestions=3)
    assert len(suggestions) == 3


def test_parse_skips_items_without_symbol() -> None:
    raw = json.dumps(
        {
            "suggestions": [
                {"rationale": "no symbol here"},
                {"symbol": "", "rationale": "blank symbol"},
                {"symbol": "AAA", "rationale": "valid"},
            ]
        }
    )
    suggestions = parse_suggestions(raw)
    assert [s.symbol for s in suggestions] == ["AAA"]


def test_parse_skips_non_dict_items() -> None:
    raw = json.dumps({"suggestions": ["not a dict", 42, {"symbol": "AAA", "rationale": "x"}]})
    suggestions = parse_suggestions(raw)
    assert [s.symbol for s in suggestions] == ["AAA"]


def test_parse_missing_rationale_yields_empty_string() -> None:
    raw = json.dumps({"suggestions": [{"symbol": "AAA"}]})
    assert parse_suggestions(raw)[0].rationale == ""


# --------------------------------------------------------------------------
# parse_suggestions — malformed input is total (never raises)
# --------------------------------------------------------------------------


def test_parse_empty_string() -> None:
    assert parse_suggestions("") == ()


def test_parse_non_json_garbage() -> None:
    assert parse_suggestions("the model said no thank you") == ()


def test_parse_json_array_not_object() -> None:
    assert parse_suggestions("[1, 2, 3]") == ()


def test_parse_object_without_suggestions_key() -> None:
    assert parse_suggestions(json.dumps({"other": "data"})) == ()


def test_parse_suggestions_not_a_list() -> None:
    assert parse_suggestions(json.dumps({"suggestions": "nope"})) == ()


def test_parse_empty_suggestions_list() -> None:
    assert parse_suggestions(json.dumps({"suggestions": []})) == ()


def test_parse_max_suggestions_zero() -> None:
    raw = _resp({"symbol": "AAA", "rationale": "x"})
    assert parse_suggestions(raw, max_suggestions=0) == ()


def test_parse_default_max_is_five() -> None:
    assert DEFAULT_MAX_SUGGESTIONS == 5
    raw = _resp(*[{"symbol": f"S{i}", "rationale": "x"} for i in range(8)])
    assert len(parse_suggestions(raw)) == 5


# --------------------------------------------------------------------------
# client.suggest_stocks — graceful no-API-key path (the common case)
# --------------------------------------------------------------------------


class _FakeSettings:
    """Stand-in for trident.settings.Settings with only the field we read."""

    def __init__(self, anthropic_api_key: str) -> None:
        self.anthropic_api_key = anthropic_api_key


def test_suggest_stocks_no_api_key_degrades(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        suggest_client, "get_settings", lambda: _FakeSettings("")
    )
    result = suggest_stocks([_cand()], PlanContext())
    assert result.ok is False
    assert result.count == 0
    assert "ANTHROPIC_API_KEY" in result.notice


def test_suggest_stocks_blank_api_key_degrades(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # A whitespace-only key counts as no key.
    monkeypatch.setattr(
        suggest_client, "get_settings", lambda: _FakeSettings("   ")
    )
    result = suggest_stocks([_cand()], PlanContext())
    assert result.ok is False
    assert "ANTHROPIC_API_KEY" in result.notice


def test_suggest_stocks_no_candidates_degrades(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # Even with a key, an empty screen short-circuits before any network call.
    monkeypatch.setattr(
        suggest_client, "get_settings", lambda: _FakeSettings("sk-test-key")
    )
    result = suggest_stocks([], PlanContext())
    assert result.ok is False
    assert result.count == 0
    assert "candidates" in result.notice.lower()


def test_suggest_stocks_never_raises_on_missing_key(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # The headline guarantee: the missing-key path returns, never raises.
    monkeypatch.setattr(
        suggest_client, "get_settings", lambda: _FakeSettings("")
    )
    result = suggest_stocks([_cand("AAA"), _cand("BBB")], PlanContext())
    assert isinstance(result, SuggestionResult)
