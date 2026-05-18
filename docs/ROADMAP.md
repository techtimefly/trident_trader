# Roadmap — from discipline harness to a tool a day trader would use

## Context

`trident_trader` is a personal, paper-only ORB bot whose win conditions are
*correctness and discipline*. Asked what blocks real day-trader use, the honest
answer is nine gaps — and the #1 wall is that the only strategy (ORB) loses money
(~−3.5%/yr, 40.6% win rate; CLAUDE.md forbids tuning it). The user wants to evolve
the app toward something a day trader would actually use, and chose the direction:

- **Add new strategies** under the existing `Strategy` protocol + honest
  comparison tooling — no app polish matters without an edge.
- **Priorities:** Breadth → Active position management → Accounting & resilience.
- **Live trading:** sketched as a final, heavily-gated phase only — not built now.
- **Out of scope:** sub-minute cadence and the SIP data feed (stay on 1-min bars +
  free IEX feed).

This roadmap sequences that into 5 phases. **Phase 1 builds the strategy-pluggability
seam everything else needs** and is the only phase specified in executable detail;
later phases are concrete on architecture but lighter, to be detailed when reached.

## Constraints (every phase)

- `src/trident/risk/gate.py` stays a **pure function** — new inputs arrive as
  defaulted dataclass fields computed by callers. All unit tests stay green.
- Inner-ring code (`risk/`, `execution/`, `portfolio/`, `safety/`, `deadman.py`)
  ships tests in the **same commit**. Outer ring (`dashboard/`, `backtest/`) does not.
- Money is `Decimal`; times stored UTC, displayed ET. `from __future__ import annotations`.
- Do **not** tune ORB (curve-fitting). New strategies under the protocol are fine.
- `paper_run.py` keeps refusing non-paper Alpaca URLs. `live_run.py` is a Phase 5
  sketch only — not created without explicit direction (CLAUDE.md).
- One logical change per commit. Migrations chain from `0007_suggestion_tables`.
- `ruff check src tests` + `mypy src` (strict) clean.

## Phase order & dependencies

1 → 2 (registry must exist before a new strategy or DB watchlist can be wired in;
Phase 1's single-source watchlist is the seam Phase 2 reroutes). 3 has no hard code
coupling to 2 but follows it per user priority. **4 depends on 3** — the `LiveTrade`
round-trip table joins entries to exits via Phase 3's order↔position linkage. 5
depends on everything and is sketch-only.

---

## Phase 1 — Strategy registry + comparison tooling  *(executable now)*

**Scope.** Make the strategy pluggable: all 5 runners and `backtest/engine.py`
select a strategy by name instead of hardcoding `OpeningRangeBreakout`. Collapse the
5-file `WATCHLIST` duplication into one module. Build a strategy-comparison CLI +
dashboard panel on the existing `summarize`/`walk_forward`. No new strategy yet —
this phase only builds the seam.

**Key facts.** `Strategy` protocol = `name: str` + `on_bar(bar, store) -> Signal|None`
(`strategies/base.py:36`). Strategies hold mutable per-day state, so the registry
maps a name to a *builder callable*, not an instance. `run_day` (`backtest/engine.py:23`)
hardcodes `OpeningRangeBreakout` at line 44; `costs`/`log` are already defaulted, so
a trailing defaulted `strategy_name` is non-breaking. `replay_runs.strategy` column
already exists — each strategy's run is independently visible in the dashboard.

**New files**
- `src/trident/strategies/registry.py` — `register(name, builder)`,
  `build_strategy(name, symbols) -> Strategy`, `available_strategies()`. Unknown
  name raises `ValueError` listing available names. Registers `orb_5m` explicitly
  (import `orb`, call `register` — no import-time side effects in `orb.py`).
- `src/trident/watchlist.py` — `WATCHLIST` constant + `resolve_watchlist() -> list[str]`
  (returns the constant in Phase 1; Phase 2 reroutes to the DB).
- `src/trident/backtest/compare.py` — pure `compare_strategies(...) -> dict[str, list[SimulatedTrade]]`.
- `scripts/compare.py` — comparison CLI (reuse `replay.py`'s bar-fetch + `fmt_money`).
- `src/trident/dashboard/templates/_compare.html` — comparison panel.
- Tests: `test_strategy_registry.py`, `test_watchlist.py`, `test_backtest_compare.py`.

**Files modified**
- `src/trident/backtest/engine.py` — `run_day` gains trailing `strategy_name: str = "orb_5m"`;
  line 44 becomes `build_strategy(strategy_name, watchlist)`.
- `scripts/{shadow_run,paper_run,replay,backtest,backfill_daily}.py` — import
  `WATCHLIST` from `trident.watchlist`; delete the 5 local constants. Runners +
  replay + backtest gain a `--strategy` arg (default `orb_5m`) → `build_strategy`.
- `src/trident/dashboard/app.py` — `/api/compare` route (`TemplateResponse(request,
  name, context)`, HTMX poll); `index.html` gains the panel.
- `src/trident/settings.py` — optional `default_strategy: str = "orb_5m"` (comment-free
  default per the `.env` gotcha).

**Commits**
1. Add `watchlist.py` + `test_watchlist.py` (no callers changed).
2. Route the 5 scripts to the shared `WATCHLIST` (pure refactor).
3. Add `strategies/registry.py` + `test_strategy_registry.py` (`orb_5m` registered).
4. Parameterize `run_day` by `strategy_name` + a regression test (ORB output unchanged).
5. Add `--strategy` to `replay.py` and `backtest.py`.
6. Add `--strategy` to `shadow_run.py` and `paper_run.py` (default `orb_5m`; paper
   non-paper-URL guard untouched).
7. Add `backtest/compare.py` + `test_backtest_compare.py`.
8. Add `scripts/compare.py`.
9. Add the dashboard comparison panel.

**Risks.** Shared strategy instance would leak `_DayState` → registry returns
builders; test asserts two builds are distinct. `run_day` arg added *after* `log`
with a default → grep `run_day(` call sites first. Comparison fairness → `compare.py`
fetches bars once and reuses them across strategies. CLAUDE.md's "watchlist
duplicated in 4 files" invariant becomes stale → flag for the user (do not self-edit
CLAUDE.md).

---

## Phase 2 — Breadth

**Scope.** Add ≥1 new strategy under the protocol that emits **long AND short**
signals (short side already works end-to-end through gate, `sizing.py`, and
`build_bracket` — confirmed; ORB just never emits shorts). Make the watchlist
DB-backed and dashboard-approved (the screener already produces candidates). Let
entry-time windows be strategy-owned, not the global ORB 9:35–11:00.

**Approach.** New strategy from disciplined literature (e.g. VWAP mean-reversion or
MA-cross) — rules chosen first, evaluated after (no curve-fitting). `resolve_watchlist()`
reads a new `watchlists` table (`symbols` JSON, `source`, `is_active`) and **falls
back to the static constant if no active row** (never resolves to empty). New
`/api/watchlist` GET/POST follows the `/api/daily-plan` mutating-form pattern
(`parse_qs`, no python-multipart) and can promote `get_latest_screen()` matches into
the active watchlist. The gate's `no_entry_before/after` become a wide outer
backstop; each strategy enforces its own intraday window inside `on_bar` (ORB keeps
its internal 11:00 cutoff — add a regression test).

**Files.** New: `strategies/<new>.py`, `persistence/watchlist_store.py`,
`templates/_watchlist.html`, migration `0008_watchlist`, strategy tests. Modified:
`registry.py`, `watchlist.py`, `dashboard/app.py`, `risk/limits.py` (wider default
window — inner-ring, tests same commit).

**Commits:** (1) `watchlists` table + migration; (2) `watchlist_store.py` + tests;
(3) reroute `resolve_watchlist()` to DB with fallback + tests; (4) watchlist panel +
screener-promote; (5) new long+short strategy + tests, registered; (6) widen gate
default window + gate tests.

**Risks.** Empty watchlist silencing signals → static fallback. Short-side
assumptions → verify `reconcile_positions` + `eod_flatten` handle signed/negative
`qty` with tests before adding the short strategy.

---

## Phase 3 — Active position management  *(the hard one)*

**Scope.** Trailing stops, scale-in, scale-out / partial exits, and manual
per-position control (close one position, adjust stop/target, cancel one order).
Today's order model is bracket-only and all-or-nothing.

**Architecture — extend the `Broker` protocol** (`execution/broker.py`). This is the
explicit recommendation: trailing stops *require* modifying a live stop leg, partial
exits *require* a single-position close, manual control *requires* single-order
cancel — none possible with only `cancel_all`/`close_all`. Add `submit_order` (single
leg), `cancel_order(id)`, `replace_order(id, ...)` (Alpaca supports it), and
`close_position(symbol, qty=None)`. `AlpacaBroker` implements them (inner-ring —
tests same commit; extend the existing fake broker).

- **Order/position model:** add order↔position linkage and a live mutable
  stop/target to `Position` (or a new `ManagedPosition` table); track bracket
  **child legs** in `Order` (only parents are tracked today).
- **Runner loop:** add an optional, defaulted `manage(bar, store, position) ->
  list[ManagementAction]|None` to the `Strategy` protocol; existing strategies
  unaffected. New frozen `ManagementAction` union: `TrailStop`/`ScaleIn`/`ScaleOut`/
  `Exit`. The runner re-evaluates each open position per bar and translates actions
  to broker calls.
- **Gate stays pure.** Scale-ins are new exposure → routed through `evaluate()` as a
  normal `Signal` (`AccountState.open_positions` already carries current exposure).
  Trail/scale-out/exit *reduce* risk → bypass the gate by design.

**Files.** New: `strategies/management.py`, migration `0009_managed_positions`,
`templates/_manage.html`, `test_broker_protocol.py`, `test_management.py`. Modified:
`execution/{broker,alpaca,orders}.py`, `strategies/base.py`, `persistence/models.py`,
`scripts/{paper_run,shadow_run}.py`, `dashboard/app.py`, `portfolio/tracking.py`.

**Commits:** (1) extend `Broker` protocol + tests; (2) `AlpacaBroker` impl + tests;
(3) single-leg `OrderIntent` + builder + tests; (4) `ManagedPosition`/leg linkage +
migration; (5) `ManagementAction` + optional `manage` protocol method + tests; (6)
per-bar re-evaluation in runners; (7) dashboard manual-control endpoints; (8)
reconcile child legs + live stop/target.

**Risks.** Trail-replace racing a fill → broker authoritative, re-read on failure,
deterministic `client_order_id`s for management orders. Bracket-leg orphaning on
partial close → replace child-leg qty atomically. EOD `close_all` must still win.

---

## Phase 4 — Accounting & resilience

**Scope.** Per-trade realized P&L (the live `Position` table has none today —
`ReplayTrade` has the shape to mirror), and crash recovery surviving a mid-session
restart.

**Approach.** New `LiveTrade` round-trip table (`symbol`, `side`, `strategy`,
entry/exit ts+price, `qty`, `gross_pnl`, `fees`, `net_pnl`, `r_multiple`,
`holding_period_seconds`, `wash_sale` bool) — written on round-trip close by joining
entry↔exit via Phase 3's linkage. Wash-sale = realized loss with a same-symbol
re-entry within 30 days (pure function over closed `LiveTrade`s; DB query, spans
sessions). Dashboard `_pnl.html` mirrors `_replay.html`.

Crash recovery: on runner startup, backfill the in-memory `BarStore` from today's
`bars` rows, then replay them through `strategy.on_bar()` with order submission
suppressed to rebuild `_DayState` (opening range) — strategies are deterministic
over their bar stream. Cross-check open positions via `reconcile_positions` (broker
authoritative). Approved-but-unsubmitted signals on restart → mark stale + log
(reject-on-doubt: price has moved).

**Files.** New: `persistence/live_trades.py`, `accounting/round_trip.py`,
`data/bars_backfill.py`, migration `0010_live_trades`, `templates/_pnl.html`,
`test_round_trip.py`, `test_bars_backfill.py`. Modified: `persistence/models.py`,
`portfolio/tracking.py` (emit `LiveTrade` on close — inner-ring, tests same commit),
`scripts/{paper_run,shadow_run}.py`, `dashboard/app.py`.

**Commits:** (1) `LiveTrade` table + migration; (2) pure round-trip + wash-sale +
tests; (3) wire `LiveTrade` creation into `tracking.py` position-close + tests; (4)
P&L panel; (5) `BarStore` backfill helper + tests; (6) startup backfill +
state-rebuild in runners; (7) handle approved-unsubmitted signals on startup.

**Risks.** State-rebuild divergence → test that rebuild matches a no-crash run.
Double `LiveTrade` rows from reconciliation → dedupe on a deterministic key. Missing
bars in backfill → if <5 OR bars present, mark symbol skipped (mirrors the live skip
path). Note: this is a P&L/wash-sale *flag*, not full tax-lot accounting.

---

## Phase 5 — Live trading  *(SKETCH ONLY — not built)*

A `scripts/live_run.py` would be a copy of `paper_run.py` with the safety property
inverted in the filename: requires a non-paper URL, refuses a paper one. Hard guards
before any submission: a `LIVE_TRADING_ENABLED` flag + a per-session confirmation
token; a hard daily-loss kill that flattens and disables for the day; a tight
max-notional ceiling; the deadman process mandatory. **Ramp gate** (precondition to
even creating the file): ≥8 weeks clean paper, a strategy *profitable net of costs*
in the honest backtest **and** forward shadow data, all inner-ring tests green, and
Phase 4 crash-recovery proven. Deliverable for this phase is a `docs/` design note +
checklist only. The dominant risk is going live before a real edge exists — hence it
is last and gated on evidence.

---

## Verification (per phase, repo-consistent)

```bash
PYTHONPATH=src python -m pytest tests/unit -q          # all unit tests stay green
ruff check src tests && mypy src                        # clean
alembic upgrade head && alembic downgrade -1 && alembic upgrade head   # each new migration
PYTHONPATH=src python scripts/replay.py --date <known-day> --strategy orb_5m   # Phase 1: byte-identical to pre-refactor
PYTHONPATH=src DASHBOARD_HOST=127.0.0.1 python scripts/run_dashboard.py         # new panels load
PYTHONPATH=src python scripts/smoke_test.py             # after persistence-model changes
```

Every signature change (`run_day`, `Broker`, `Strategy`) keeps defaults so existing
call sites and tests are unaffected. After every runner edit, confirm `paper_run.py`
still refuses non-paper URLs.

## Critical files

- `src/trident/strategies/base.py`, `src/trident/strategies/registry.py` (new)
- `src/trident/backtest/engine.py` (`run_day`), `src/trident/watchlist.py` (new)
- `src/trident/execution/broker.py`, `execution/alpaca.py`, `execution/orders.py`
- `src/trident/persistence/models.py`, `src/trident/portfolio/tracking.py`
- `scripts/paper_run.py`, `scripts/shadow_run.py`, `scripts/replay.py`,
  `scripts/backtest.py`, `scripts/backfill_daily.py`
- `src/trident/dashboard/app.py`

## Open decisions (resolve when the phase is reached)

- **Phase 2:** which specific new strategy (VWAP mean-reversion vs MA-cross vs other
  literature setup) — chosen at Phase 2 start, rules-first.
- **Phase 3:** `ManagedPosition` as a new table vs extending `Position` in place.
- **CLAUDE.md** is owned by the user — Phase 1 makes its "watchlist duplicated"
  invariant stale and Phase 2/3 expand the run-mode and protocol notes; flag these
  for the user rather than self-editing.
