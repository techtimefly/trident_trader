# Roadmap progress

Source of truth for the remote roadmap routine. **One unchecked item is
completed per run.** See `docs/ROADMAP.md` for the full plan and `CLAUDE.md`
for the rules every change must follow.

Legend: `- [ ]` to do · `- [x]` done · `- [!]` blocked (reason inline).

## Phase 1 — Strategy registry + comparison tooling
- [x] Complete — pluggable strategy registry + comparison tooling (commit 29e923a).

## Phase 2 — Breadth
- [x] 2.1 Add the `watchlists` table + Alembic migration `0008_watchlist` (chains from `0007_suggestion_tables`).
- [x] 2.2 Add `src/trident/persistence/watchlist_store.py` (DB read/write helpers) + tests.
- [x] 2.3 Reroute `resolve_watchlist()` in `src/trident/watchlist.py` to the DB store with a static fallback — never resolve to empty + tests.
- [x] 2.4 Add the dashboard `/api/watchlist` GET/POST panel (`_watchlist.html`) with a screener-promote action.
- [x] 2.5 Add one new strategy under the `Strategy` protocol that emits both long and short signals; register it in the registry; ship tests.
- [ ] 2.6 Widen the gate's default entry-time window in `RiskLimits` to a wide outer bound + update gate tests (inner-ring — tests same commit). ORB keeps its own 11:00 cutoff.

## Phase 3 — Active position management
- [ ] 3.1 Extend the `Broker` protocol with `submit_order` / `cancel_order` / `replace_order` / `close_position` + protocol/fake-broker tests.
- [ ] 3.2 Implement the new methods in `AlpacaBroker` + tests.
- [ ] 3.3 Add a single-leg `OrderIntent` + builder in `execution/orders.py` + tests.
- [ ] 3.4 Add the `ManagedPosition` table / order-leg linkage + migration `0009`.
- [ ] 3.5 Add the `ManagementAction` union + an optional `manage()` method on the `Strategy` protocol + tests.
- [ ] 3.6 Add per-bar open-position re-evaluation to the runners; scale-ins routed through the pure gate.
- [ ] 3.7 Add dashboard manual-control endpoints (close one position, adjust stop/target, cancel one order).
- [ ] 3.8 Update reconciliation to track bracket child legs and the live stop/target.

## Phase 4 — Accounting & resilience
- [ ] 4.1 Add the `LiveTrade` round-trip table + migration `0010`.
- [ ] 4.2 Add `src/trident/accounting/round_trip.py` — pure round-trip + wash-sale computation + tests.
- [ ] 4.3 Wire `LiveTrade` creation into `portfolio/tracking.py` position-close (inner-ring — tests same commit).
- [ ] 4.4 Add the per-trade P&L dashboard panel (`_pnl.html`).
- [ ] 4.5 Add `src/trident/data/bars_backfill.py` — BarStore DB-backfill helper + tests.
- [ ] 4.6 Add startup BarStore backfill + strategy-state rebuild to the runners.
- [ ] 4.7 Handle approved-but-unsubmitted signals on startup (mark stale + log).

## Phase 5 — Live trading sketch (docs only)
- [ ] 5.1 Write `docs/LIVE_TRADING.md` — the gated real-money design note + ramp checklist. No code; do NOT create `live_run.py`.

## Final
- [ ] R.1 Full code review of the whole application; score each feature from the perspective of a real day trader; write the result to `docs/DAY_TRADER_REVIEW.md`.
