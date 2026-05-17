"""AI stock-suggestions — an advisory pre-market precheck.

Runs the latest stock-screener output through Claude (the Anthropic API), which
suggests a small, focused set of stocks for you to WATCH today, with reasoning.
Prints the suggestions and persists the run so the dashboard panel can show it.

This is Phase 3 of the roadmap. The output is **advisory only** — a list you
read before deciding what to do. It is deliberately NOT wired into the risk
gate, the runners, the watchlist, or the order flow. The human stays in the
loop.

Requires a screen to have run first (`scripts/screen.py`) and an
`ANTHROPIC_API_KEY` to be configured. With no key, the script exits cleanly
with a clear message — it never traces back.

Examples:
    PYTHONPATH=src python scripts/suggest.py
    PYTHONPATH=src python scripts/suggest.py --max 3
    PYTHONPATH=src python scripts/suggest.py --no-persist
"""
from __future__ import annotations

import argparse

from trident.audit.log import configure_logging, get_logger
from trident.screener.persistence import get_latest_screen
from trident.suggest.client import suggest_stocks
from trident.suggest.persistence import save_suggestions
from trident.suggest.suggestion import PlanContext, SuggestionResult


def print_report(result: SuggestionResult) -> None:
    print("\n" + "=" * 72)
    print("AI STOCK SUGGESTIONS  —  ADVISORY ONLY, NOT A TRADING INSTRUCTION")
    print("=" * 72)

    if not result.ok:
        print(f"\n{result.notice}\n")
        return

    print(f"  model: {result.model}")
    print(f"  {result.count} symbol(s) suggested to watch\n")
    print(f"{'#':>4}  {'Symbol':<8}{'Conf.':<10}Rationale")
    print("-" * 72)
    for s in result.suggestions:
        print(f"{s.rank:>4}  {s.symbol:<8}{s.confidence:<10}{s.rationale}")
    print("-" * 72)
    print("\nThese are suggestions to watch — you decide what to do with them.\n")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="AI stock-suggestions pre-market precheck (advisory only)."
    )
    parser.add_argument(
        "--max",
        type=int,
        default=5,
        help="Maximum number of symbols to suggest. Default: 5.",
    )
    parser.add_argument(
        "--no-persist",
        action="store_true",
        help="Skip writing the run to the database (the dashboard won't see it).",
    )
    args = parser.parse_args()

    configure_logging()
    log = get_logger("suggest")

    if args.max < 1:
        log.error("invalid_max", max=args.max)
        print("--max must be at least 1.")
        return 1

    print("Loading the latest screen...")
    try:
        latest = get_latest_screen()
    except Exception:
        log.exception("suggest_screen_load_failed")
        print("Could not load the latest screen — check the database connection.")
        return 1

    if latest is None:
        print(
            "No screen run found. Run scripts/screen.py first, then re-run "
            "this precheck."
        )
        return 1

    # The screener does not carry a daily plan; pass an empty PlanContext.
    # The plan is advisory context for the model, not a hard input — and the
    # dashboard remains the place the user sets and sees their plan.
    plan = PlanContext()

    print(
        f"Reviewing {latest.matched} screened symbol(s) with the AI "
        "(this calls the Anthropic API)..."
    )
    result = suggest_stocks(latest.matches, plan, max_suggestions=args.max)
    print_report(result)

    if not args.no_persist:
        try:
            run_id = save_suggestions(result=result, screen_run_id=latest.run_id)
        except Exception:
            log.exception("suggest_persist_failed")
            print("Could not save the suggestion run — check the database.")
            return 1
        log.info("suggest_persisted", run_id=str(run_id), ok=result.ok)
        print(f"Saved suggestion run {run_id}. Open the dashboard to view.")

    # A degraded (not-ok) run — most commonly a missing API key — is a clean
    # exit with a clear message, not an error: the run was handled, persisted,
    # and reported. Exit 0 so the agent's pre-market precheck does not look
    # like a crash when the key simply is not set.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
