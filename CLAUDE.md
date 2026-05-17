# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A personal automated day-trading bot. **Paper trading only.** Single user, single
watchlist, intentionally minimal — the README is for humans setting up the project;
this file is for agents changing it. The win conditions are correctness and
discipline, not feature breadth or returns.

Not a product. Do not propose monetization, multi-tenant features, auth for other
users, mobile apps, or marketing. If a request implies any of that, stop and ask.

## Working branch

Development happens on `claude/review-trading-app-notes-S6HQY`. Do not push to
`main` or any other branch without explicit permission. Branch off the working
branch only when asked.

## Strategy status (read before "improving" anything)

A 250-day replay showed the current ORB strategy is **unprofitable**: ~40.6% win
rate, ~-3.5% over the year, avg R ~-0.10. The framework is sound; the strategy is
not.

- **Do not enable `paper_run.py` or any live-execution flow** by default or in CI.
  The user explicitly chooses when to forward-test or swap strategies.
- If asked to "improve" the strategy, push back: tuning parameters against the same
  250-day data is curve-fitting and degrades forward performance. Legitimate paths
  are (a) a different strategy under the same `Strategy` protocol, (b) a disciplined
  ORB variant from the literature, or (c) several weeks of fresh shadow data before
  any change.

## Architecture

Bars flow in from Alpaca, a strategy proposes trades, a pure risk gate vets them,
and (only in paper mode) approved trades become bracket orders:

```
AlpacaBarFeed (WebSocket, IEX)
  ├─ closed 1-min Bar ─► BarStore (in-memory ring buffer, per symbol/timeframe)
  │                   └► persist_bar ─► Postgres `bars`
  └─ on_bar handler ─► Strategy.on_bar(bar, store) ─► Signal | None
                                                         │
              AccountState + MarketState + RiskLimits ───┤
                                                         ▼
                                    risk.gate.evaluate() ─► GateDecision
                                                         │  (paper_run only,
                                                         ▼   if approved)
                              build_bracket() ─► BracketOrderIntent
                                                         ▼
                          AlpacaBroker.submit_bracket()  (paper account)
```

`paper_run.py` additionally runs four background loops: heartbeat (5s), order
polling (`sync_orders`, 10s), position reconciliation (`reconcile_positions`, 60s),
and a one-shot EOD flatten 5 minutes before the close.

### Concentric rings — rigor scales inward

- **Inner ring — must never fail silently.** `src/trident/risk/`,
  `src/trident/execution/`, `src/trident/portfolio/`, `src/trident/safety/`,
  `scripts/deadman.py`. New inner-ring code ships with tests in the same commit.
- **Middle ring — failures degrade the product, not capital.**
  `src/trident/strategies/`, `src/trident/data/`, `src/trident/persistence/`.
- **Outer ring — can fail without losing money.** `src/trident/dashboard/`,
  `src/trident/backtest/`.

`src/trident/risk/gate.py` is the single most important file: a pure function,
first-failure short-circuit, reject-on-doubt. Every branch is unit-tested. Change
it carefully.

### Run modes — the safety property is in the filename

- `scripts/shadow_run.py` — live data, signals + gate evaluated, **never submits
  orders**.
- `scripts/paper_run.py` — submits bracket orders to the **paper** account. Refuses
  non-paper Alpaca URLs at construction (`AlpacaBroker.__init__`).
- `scripts/deadman.py` — a **separate process** (intentionally not in-process with
  the runner). Flattens everything if the runner's heartbeat goes stale (>45s) and
  open positions/orders exist. Always run it alongside `paper_run.py`.
- `scripts/replay.py` — feeds historical 1-min bars through the same strategy +
  gate, then simulates fills. Not a real backtest (idealistic fills, no slippage/
  fees); for sanity-checking only. The honest harness lands in v0.3.

There is no `live_run.py`. Do not create one without explicit direction.

### Key invariants

- **Money is `Decimal`, always.** Never `float`, never mixed.
- **Times stored UTC, displayed ET** (`trident.clock.ET`).
- **Bracket idempotency:** `client_order_id = f"trident-{signal_id}"`
  (`execution/orders.py`). Resubmitting a signal cannot double-submit on Alpaca.
- **Reconciliation treats the broker as authoritative.** If the local DB and Alpaca
  disagree, update the local DB — never submit compensating orders to "correct"
  Alpaca.
- **`audit_events` is append-only** — a DB trigger blocks UPDATE/DELETE. To change
  state, write a new event.
- The **kill switch** lives in the `system_state` table; the dashboard toggles it
  and the runner reads it before every gate evaluation, no restart needed.
- The **watchlist is a `WATCHLIST` constant duplicated** in `shadow_run.py`,
  `paper_run.py`, `replay.py`, and `backfill_daily.py` — changing it means editing
  each.

## Common commands

```bash
# Setup (one-time)
docker compose up -d postgres
pip install -e ".[dev]"
alembic upgrade head

# Tests — must pass before any commit; run in <1s, no network/DB needed
PYTHONPATH=src python -m pytest tests/unit -q

# A single file / single test / keyword match
PYTHONPATH=src python -m pytest tests/unit/test_risk_gate.py -q
PYTHONPATH=src python -m pytest tests/unit/test_risk_gate.py::test_name -q
PYTHONPATH=src python -m pytest tests/unit -q -k "sizing"

# Lint + type (both must be clean)
ruff check src tests
mypy src

# Smoke test (checks DB + Alpaca credentials; submits nothing)
PYTHONPATH=src python scripts/smoke_test.py

# Replay historical days (writes to DB; dashboard picks it up)
PYTHONPATH=src python scripts/replay.py --days 90
PYTHONPATH=src python scripts/replay.py --date 2026-05-12 --no-persist

# Shadow runner (live data, no orders)
PYTHONPATH=src python scripts/shadow_run.py

# Dashboard (then open http://127.0.0.1:8765)
PYTHONPATH=src python scripts/run_dashboard.py
```

After any persistence-model change, run `alembic upgrade head` before restarting
any process — skipping it makes dashboard endpoints 500 with "relation does not
exist".

## Test policy

- ~80 unit tests; they run in <1s and need no network or database.
- Risk gate, position sizing, ORB, EOD timing, and the fill simulator have
  exhaustive branch coverage.
- Inner-ring code (gate, execution, safety, portfolio) must ship with tests in the
  same commit. Outer-ring code (dashboard, replay UI) does not require test parity.

## Things that have bitten us (do not repeat)

1. **Inline comments in `.env`.** `pydantic-settings` reads the whole right-hand
   side as the value. Keep comments on their own lines. `settings.py` has defensive
   validators (`_strip_trailing_comment`, `_blank_or_comment_to_none`); new vars
   need either a validator or a comment-free default.
2. **Alpaca free tier requires the IEX feed.** Historical bar requests default to
   SIP and 403. Always pass `feed=DataFeed.IEX` (or read `settings.alpaca_data_feed`).
3. **Starlette `TemplateResponse` signature.** Use
   `templates.TemplateResponse(request, name, context)`. The other order raises
   `TypeError: unhashable type: 'dict'` (the context dict becomes a Jinja cache
   key). All dashboard endpoints follow this.
4. **Codespaces port-forwarding.** `scripts/run_dashboard.py` binds `0.0.0.0` by
   default so forwarded ports work; respect the `DASHBOARD_HOST` override. (The
   README's "binds to 127.0.0.1" line is stale — the launcher is authoritative.)
5. **ORB target geometry.** `target = entry + (entry - stop)` — a true 1R from
   entry. The earlier `target = entry + (OR_high - OR_low)` biased realized R to
   ~0.6 because the breakout bar closes above OR_high.
6. **Notional cap sizes down, doesn't reject.** When the risk-budget share count
   exceeds the `max_position_notional_pct` cap, take `min(by_risk, by_notional)`.
   Reject only if even one share blows the cap.

## Style & conventions

- Python 3.12; `from __future__ import annotations` everywhere.
- `ruff` clean and `mypy --strict` clean (config in `pyproject.toml`).
- Pure functions for safety-critical logic; side effects only at the edges.
- Persistence is Postgres only (SQLAlchemy 2.0 + Alembic). No SQLite fallback.

## Commits

- One logical change per commit; imperative subject; body explains the *why*.
- Never put model identifiers (e.g. `claude-opus-*`) in commit messages, PR
  descriptions, code comments, or any committed artifact.
- Do not create PRs unless the user explicitly asks.
