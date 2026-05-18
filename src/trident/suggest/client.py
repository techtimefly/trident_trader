"""The Anthropic-API client layer for the AI stock-suggestions feature.

This is the only module in :mod:`trident.suggest` that touches the network.
All pure logic — prompt construction and response parsing — lives in
:mod:`trident.suggest.prompt`; this module just wires it to the Anthropic SDK.

**Graceful degradation is the headline behaviour.** When no credentials are
configured, :func:`suggest_stocks` returns a clear not-ok
:class:`~trident.suggest.suggestion.SuggestionResult` instead of raising.
An API error or an unparseable response degrades the same way. The feature is
advisory and outer-ring — it must never crash a caller.

Credentials are resolved in priority order by the Anthropic SDK:
``ANTHROPIC_API_KEY`` (pay-per-token) → ``CLAUDE_CODE_OAUTH_TOKEN`` (Claude
Code subscription). Set either one; do not pass both.
"""
from __future__ import annotations

import os
from collections.abc import Sequence
from typing import Any

from trident.audit.log import get_logger
from trident.screener.criteria import ScreenCandidate
from trident.settings import get_settings
from trident.suggest.prompt import (
    DEFAULT_MAX_SUGGESTIONS,
    build_user_prompt,
    has_candidates,
    parse_suggestions,
    system_prompt,
)
from trident.suggest.suggestion import PlanContext, SuggestionResult

log = get_logger("suggest.client")

# A personal-tool pre-market precheck: a short suggestion list with brief
# rationales. claude-haiku-4-5-20251001 is used when authenticating via the
# Claude Code subscription (CLAUDE_CODE_OAUTH_TOKEN); Sonnet hits the
# subscription's API rate cap. With a paid ANTHROPIC_API_KEY, any model works.
DEFAULT_MODEL = "claude-haiku-4-5-20251001"

# The suggestion JSON is small; this ceiling is generous headroom and keeps
# the request well under any SDK HTTP timeout (no streaming needed).
_MAX_TOKENS = 1024


def _build_client(api_key: str, oauth_token: str) -> Any:
    """Construct the Anthropic SDK client. Import is local so the SDK is only
    needed when credentials are actually present.

    The SDK reads ``ANTHROPIC_API_KEY`` and ``ANTHROPIC_AUTH_TOKEN`` from the
    environment. ``CLAUDE_CODE_OAUTH_TOKEN`` is a Claude Code convention that
    the SDK does not read directly, so we pass it explicitly as ``auth_token``.
    """
    import anthropic

    if api_key:
        return anthropic.Anthropic(api_key=api_key)
    if oauth_token:
        return anthropic.Anthropic(auth_token=oauth_token)
    return anthropic.Anthropic()


def _extract_text(response: Any) -> str:
    """Concatenate the text content blocks of a Messages API response."""
    parts: list[str] = []
    for block in getattr(response, "content", []):
        if getattr(block, "type", None) == "text":
            parts.append(getattr(block, "text", ""))
    return "".join(parts)


def suggest_stocks(
    candidates: Sequence[ScreenCandidate],
    plan: PlanContext,
    *,
    max_suggestions: int = DEFAULT_MAX_SUGGESTIONS,
    model: str = DEFAULT_MODEL,
) -> SuggestionResult:
    """Ask Claude to review screener ``candidates`` and suggest stocks to watch.

    Returns a :class:`SuggestionResult`. The result is **ok** with parsed
    suggestions on success, and a **not-ok degraded** result in every failure
    mode — this function does not raise:

    - no credentials configured            -> not-ok, clear notice
    - no screener candidates to review     -> not-ok, clear notice
    - the Anthropic API errors             -> not-ok, error logged
    - the model reply will not parse       -> not-ok, clear notice

    ``plan`` is the user's daily-plan context (advisory only — it just helps
    the model keep the list focused). ``max_suggestions`` caps the list size;
    ``model`` selects the Anthropic model.

    The system prompt is sent as a cacheable block (``cache_control``) so that
    repeated runs in a session only pay full price for the volatile screener
    table, not the frozen instructions.
    """
    settings = get_settings()
    api_key = settings.anthropic_api_key.strip()
    oauth_token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "").strip()
    if not api_key and not oauth_token:
        log.info("suggest_no_credentials")
        return SuggestionResult.degraded(
            "No Anthropic credentials configured — AI suggestions are unavailable. "
            "Set ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN to enable the "
            "pre-market precheck."
        )

    if not has_candidates(candidates):
        log.info("suggest_no_candidates")
        return SuggestionResult.degraded(
            "The latest screen has no candidates to review — run the screener "
            "first, then re-run the suggestion precheck."
        )

    user_prompt = build_user_prompt(candidates, plan, max_suggestions=max_suggestions)
    try:
        client = _build_client(api_key, oauth_token)
        response = client.messages.create(
            model=model,
            max_tokens=_MAX_TOKENS,
            system=[
                {
                    "type": "text",
                    "text": system_prompt(),
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_prompt}],
        )
    except Exception:
        # Outer-ring: an API error degrades the panel, it never crashes a
        # caller. The full traceback goes to the log for the human to see.
        log.exception("suggest_api_error", model=model)
        return SuggestionResult.degraded(
            "The AI suggestion request failed — see the logs. The screener "
            "output is unaffected."
        )

    raw_text = _extract_text(response)
    allowed = [c.symbol for c in candidates]
    suggestions = parse_suggestions(
        raw_text, allowed_symbols=allowed, max_suggestions=max_suggestions
    )
    if not suggestions:
        log.info("suggest_empty_result", model=model)
        return SuggestionResult.degraded(
            "The AI reviewed the screen but suggested no symbols to watch."
        )

    log.info("suggest_ok", model=model, count=len(suggestions))
    return SuggestionResult(suggestions=suggestions, ok=True, notice="", model=model)
