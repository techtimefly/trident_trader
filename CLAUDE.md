# CLAUDE.md

Project memory for Claude Code (and other agents). Read this first; the
README is for humans setting up the project, this file is for AI agents
making changes to it.

## What this is

Personal automated day-trading bot. **Paper trading only.** Single user,
single watchlist, intentionally minimal. The win conditions are correctness
and discipline, not feature breadth or returns.

Not a product. Do not propose monetization, multi-tenant features, auth
flows for other users, mobile apps, or marketing. If a request implies any
of those, stop and ask first.

## Working branch

All development happens on `claude/review-trading-app-notes-S6HQY`. Do not
push to `main` or any other branch without explicit permission. Create new
branches off the working branch only when the user asks.

## Strategy status (important)

The 250-day replay (commit `45b5564`) showed the current ORB strategy is
**unprofitable**: 40.6% win rate, -3.5% over a year, avg R -0.10. The
framework is sound; the strategy is not.

**Do not enable `paper_run.py` or any live execution flow** as a default
or in CI. The user will explicitly choose whether to forward-test or
swap strategies.

If you are asked to "improve" the strategy, push back: parameter tuning
on the same 250-day data is curve-fitting and will make forward
performance worse, not better. The legitimate paths are (a) implement a
different strategy under the same `Strategy` protocol, (b) implement a
disciplined ORB variant from the literature, or (c) gather forward-test
shadow data for several weeks before any change.

## Architecture (concentric rings)

The system is intentionally three rings; engineering rigor scales inward.

- **Outer ring (UI, replay UI, audit panels).** Can fail without losing
  money. `src/trident/dashboard/`, `src/trident/backtest/`.
- **Middle ring (strategy, data feed, persistence).** Failures degrade
  the product but do not risk capital. `src/trident/strategies/`,
  `src/trident/data/`, `src/trident/persistence/`.
- **Inner ring (risk gate, execution, position service, dead-man).**
  Must never fail silently. `src/trident/risk/`, `src/trident/execution/`,
  `src/trident/portfolio/`, `src/trident/safety/`, `scripts/deadman.py`.

The risk gate (`src/trident/risk/gate.py`) is the single most important
file. Pure function. Every branch is unit-tested. Change it carefully.

## Mode separation

Two runners with the safety property visible in the filename:

- `scripts/shadow_run.py` — never submits orders. Use for live data
  observation.
- `scripts/paper_run.py` — submits bracket orders to the **paper**
  account. Refuses non-paper Alpaca URLs at construction time.

There is no `live_run.py`. Do not create one without explicit user
direction, and not before the strategy proves itself in shadow over many
weeks.

`scripts/deadman.py` is a separate process (intentionally not in-process
with the runner) that flattens positions if the runner's heartbeat goes
stale. Always run it alongside `paper_run.py`.

## Things that have bitten us (do not repeat)

1. **Inline comments in `.env`.** `pydantic-settings` reads the entire
   right-hand side as the value. Keep comments on their own lines.
   `settings.py` has defensive validators, but don't add new vars without
   either a validator or a comment-free default.

2. **Alpaca free tier requires IEX feed.** Historical bar requests
   default to SIP and 403. Always pass `feed=DataFeed.IEX` (or read from
   `settings.alpaca_data_feed`). See `scripts/backfill_daily.py` and
   `scripts/replay.py`.

3. **Starlette `TemplateResponse` signature.** The right form is
   `templates.TemplateResponse(request, name, context)`. The other order
   yields a `TypeError: unhashable type: 'dict'` because the context dict
   gets used as a Jinja cache key. All endpoints must follow this.

4. **Codespaces port-forwarding.** Bind the dashboard to `0.0.0.0`, not
   `127.0.0.1`, or the forwarded port can't reach it. The launcher
   defaults to `0.0.0.0`; respect `DASHBOARD_HOST` env override.

5. **ORB target geometry.** `target = entry + (entry - stop)` — a true
   1R from entry. The earlier `target = entry + (OR_high - OR_low)` was
   wrong because the breakout bar's close sits above OR_high, biasing
   realized R to ~0.6.

6. **Notional cap sizes down, doesn't reject.** When the risk-budget
   share count exceeds the `max_position_notional_pct` cap, take
   `min(by_risk, by_notional)`. Only reject if even one share blows the
   cap.

7. **Migrations after pull.** When persistence models change, always
   `alembic upgrade head` before restarting any process. Symptom of
   skipping it: dashboard endpoints 500 with "relation does not exist".

8. **Reconciliation treats the broker as authoritative.** If local DB
   and Alpaca disagree, update the local DB. Never submit compensating
   orders to "correct" Alpaca.

## Local commands

```bash
# Setup (one-time)
docker compose up -d postgres
pip install -e ".[dev]"
alembic upgrade head

# Tests (must pass before any commit)
PYTHONPATH=src python -m pytest tests/unit -q

# Lint + type
ruff check src tests
mypy src

# Replay (writes to DB, dashboard picks up)
PYTHONPATH=src python scripts/replay.py --days 90

# Shadow runner (no orders)
PYTHONPATH=src python scripts/shadow_run.py

# Dashboard
PYTHONPATH=src python scripts/run_dashboard.py
```

## Test policy

- 80 unit tests at present; they all run in <1 second and require no
  network or database.
- Risk gate, position sizing, ORB, EOD timing, fill simulator have
  exhaustive branch coverage.
- New code in the inner ring (gate, execution, safety, portfolio) must
  ship with tests in the same commit.
- Outer-ring code (dashboard, replay UI) does not require test parity.

## Database

Postgres only. The `audit_events` table is **append-only** — there is a
DB trigger that blocks UPDATE/DELETE. Don't try to mutate audit rows;
write a new event instead.

## Style

- Python 3.12, `from __future__ import annotations` everywhere.
- `ruff` + `mypy --strict` clean.
- Decimal for money. Never float. Never mix.
- Times stored UTC, displayed ET (`trident.clock.ET`).
- Pure functions for safety-critical logic. Side effects at the edges.

## Commits

- One logical change per commit.
- Imperative subject, body explaining the *why*.
- Never include identifiers like `claude-opus-*` in commit messages,
  PR descriptions, code comments, or any artifact pushed to the repo.
- Do not create PRs unless the user explicitly asks.
