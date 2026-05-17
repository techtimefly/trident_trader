# Trident Trader — code review, scored from a day trader's chair

A full review of the application after roadmap Phases 1–5, scored from the
perspective of someone who actually trades intraday for a living. Honest, not
generous.

**Facts as reviewed:** 319 unit tests pass; `ruff`/`mypy` clean on all changed
code (9 ruff + 10 mypy errors remain, all pre-existing in `data/feed.py`,
`execution/alpaca.py`, `audit/log.py`, `test_orb.py` — untouched); 10 Alembic
migrations apply and roundtrip; all 10 dashboard panels serve 200.

## Verdict up front

This is a **well-engineered discipline harness, now a genuinely capable
platform** — but it is still **not something a day trader can make money with
today**. The build quality is high: a pure, exhaustively-tested risk gate;
clean concentric-ring architecture; honest backtesting; real crash recovery.
What it lacks is the only thing that ultimately matters — **a strategy with a
proven edge**. Score it as a platform to *develop* an edge on, and it is strong.
Score it as a money-maker, and it is not one yet.

## Feature scorecard

| Area | Score | The honest read |
|---|---|---|
| Risk gate | 9/10 | Pure function, first-failure short-circuit, reject-on-doubt, every branch tested. The best part of the codebase. A day trader can trust it not to do something stupid. |
| Resilience & safety | 8/10 | Deadman switch, broker-authoritative reconciliation, EOD flatten, mid-session crash recovery (BarStore + strategy-state rebuild), stale-signal relabelling. Genuinely production-minded. |
| Execution layer | 7/10 | Clean `Broker` abstraction: bracket + single-leg orders, `replace_order`, `cancel_order`, partial `close_position`. The Alpaca adapter is the network edge — integration-tested only. |
| Dashboard / UX | 7/10 | 10 live HTMX panels: equity, plan, watchlist, positions, signals, orders, P&L, strategy comparison, manual controls, screen/AI. Fine for a single-user cockpit; degrades gracefully. |
| Accounting | 6/10 | `LiveTrade` round-trips with realized P&L, R-multiple, holding period, a wash-sale flag, and a P&L panel. **But:** live per-order fees are not tracked (`fees = 0`), exit-matching is a heuristic, and there is no tax-lot accounting. |
| Active position management | 6/10 | Trailing stops, scale-out, manual close/cancel/adjust — capability built and tested. **Gaps:** no built-in strategy emits management actions; `ScaleIn` is recognised but not executed; **a `TrailStop` updates the recorded stop but the manage loop does not yet call `broker.replace_order` on the live bracket stop leg** — so a trail is, today, advisory in the live path. |
| Breadth | 6/10 | A long+short strategy now exists (`vwap_reversion`); the watchlist is DB-backed, dashboard-editable and screener-promotable, and consumed by the runners; the gate window spans the full session. Still anchored to a 6-symbol default and the thin IEX universe. |
| Data & speed | 3/10 | Unchanged and out of scope by request: IEX feed only (~2–3% of consolidated volume, no bid/ask), 1-minute bars, polling loops. For an active day trader this is the hard ceiling — fine for disciplined intraday swings, not for scalping or fast momentum. |
| Strategy / edge | 3/10 | The wall. `orb_5m` is measured unprofitable (~40.6% win, ~−3.5%/yr). `vwap_reversion` is new and **unevaluated on out-of-sample data**. The registry + honest backtest + comparison CLI make finding an edge *much* easier — but an edge is not yet found. |
| Going live | n/a | Correctly paper-only. `docs/LIVE_TRADING.md` specifies a real ramp gate (profitable strategy net of costs, ≥8 weeks clean paper, owner sign-off). The discipline here is exactly right; the capability to trade real money is, by design, zero. |

## The nine original blockers — revisited

When first asked "what would prevent a day trader from using this", nine gaps
were named. After Phases 1–5:

| # | Original blocker | Status |
|---|---|---|
| 1 | Strategy doesn't make money | **Unchanged.** Still the #1 wall. The path to fixing it is now far better paved (registry, honest compare). |
| 2 | Paper-only | **By design.** Live is a gated design note, not code. |
| 3 | Long-only, 6 symbols, before 11am | **Largely closed.** Long+short strategy, dynamic watchlist, full-session gate window. |
| 4 | Thin IEX data, no bid/ask | **Unchanged** — deprioritised by request. |
| 5 | 1-min polling cadence | **Unchanged** — deprioritised by request. |
| 6 | PDT $25k capital floor | **Unchanged.** The day-trade cap enforces discipline; it is not a workaround. |
| 7 | No active position management | **Closed (capability).** Trailing/scale/manual control built + tested; not yet exercised by a strategy, and the live-stop wire is pending. |
| 8 | No P&L / tax / wash-sale | **Closed.** `LiveTrade` round-trips, wash-sale flag, P&L panel — minus live fee tracking. |
| 9 | Operational fragility on restart | **Closed.** BarStore backfill + strategy-state rebuild + stale-signal handling. |

## Highest-leverage next steps (in order)

1. **Find an edge.** A strategy with positive expectancy *net of costs* in the
   honest backtest **and** in forward shadow data. Nothing else moves the needle
   until this exists. The platform is now ready to support the search.
2. **Wire the trailing stop to the broker.** Have the manage loop look up the
   bracket stop leg (now tracked via `parent_order_id`) and call
   `broker.replace_order` — so a `TrailStop` actually moves the live protective
   order, not just the recorded one.
3. **Track live fees** so `LiveTrade.net_pnl` is truthful.
4. **Evaluate `vwap_reversion`** on out-of-sample data before it is trusted.

## Bottom line

Could a day trader use this **today** to make money? **No** — no proven edge,
paper-only, slow data. Is it a **sound platform to develop and validate an edge
on**? **Yes — considerably so**, and markedly more capable than at the start of
this roadmap. The engineering discipline (pure risk gate, honest backtesting,
crash recovery, reject-on-doubt everywhere) is exactly what you want underneath
a trading system. The missing piece is not infrastructure — it is the edge.
