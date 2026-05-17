# Live trading — gated real-money path (design note)

> **Status: design only. No code. `live_run.py` does not exist and must not be
> created without explicit, written direction from the account owner.**

This note specifies *how* a real-money run mode would be built, and — more
importantly — the bar it must clear first. It is Phase 5 of the roadmap. The
dominant risk in this whole project is going live before there is an edge; this
document exists so that decision is made deliberately, against evidence, not by
drift.

## Why this is not built

`paper_run.py` refuses any non-paper Alpaca URL at construction. There is no
`live_run.py`. That is intentional: the current strategies are not proven
profitable net of costs (the honest backtest of ORB is negative; VwapReversion
is unevaluated on out-of-sample data). Wiring real money to an unproven edge
loses real money. Build the edge first; the plumbing below is the easy part.

## Ramp gate — ALL must hold before `live_run.py` is even created

1. **A profitable strategy, honestly measured.** Positive expectancy *net of
   costs* in `scripts/backtest.py` (slippage + fees modeled) **and** in forward
   `shadow_run.py` data the strategy never saw. Not curve-fit to the backtest.
2. **≥ 8 weeks of clean paper trading** on that strategy — `paper_run.py`
   running daily with no unexplained reconciliation drift, no gate bypasses,
   no crash that the recovery path (Phase 4.6/4.7) did not handle cleanly.
3. **Inner-ring tests green** — `risk/`, `execution/`, `portfolio/`, `safety/`
   fully passing, and `ruff` + `mypy` clean on changed files.
4. **Crash recovery proven** — a mid-session restart of `paper_run.py`
   demonstrably resumes: BarStore + strategy state rebuilt, positions
   reconciled, no double entry, stale signals relabelled.
5. **Owner sign-off** — the account owner explicitly authorises live trading,
   in writing, with a stated initial capital figure.

If any item is not true, the answer is "not yet". There is no partial live.

## What `live_run.py` would be

A near-copy of `paper_run.py` with the safety property inverted in the
filename, exactly as the run-mode convention demands:

- **Requires a non-paper Alpaca URL; refuses a paper URL** — the mirror of
  `paper_run.py`'s current guard. A live runner pointed at paper is as much a
  bug as a paper runner pointed at live.
- Reuses the *entire* existing stack unchanged: the pure `risk/gate.py`, the
  strategy registry, `execution/`, `portfolio/`, the EOD flatten, the deadman.
  Live trading must not introduce a second, divergent code path — only a
  different broker endpoint and tighter limits.

## Hard guards before any live submission

- **`LIVE_TRADING_ENABLED` env flag** — absent or false ⇒ the runner refuses to
  start. Not a default; an explicit opt-in per machine.
- **Per-session confirmation token** — a value the owner supplies at launch
  (not stored), so an unattended restart cannot silently resume live.
- **Hard daily-loss kill** — a max daily drawdown that, when hit, flattens
  everything and disables new entries for the rest of the day. Tighter than the
  paper `daily_loss_limit_pct`.
- **Tight max-notional ceiling** — a per-position and per-day notional cap far
  below the paper defaults, sized to the *stated initial capital*, not equity.
- **Deadman mandatory** — `scripts/deadman.py` must be confirmed running before
  the live runner will start; on paper it is merely recommended.
- **Tiny initial capital** — start at an amount whose total loss is acceptable;
  ramp only on evidence.
- **Manual daily go/no-go** — live trading is opt-in each day, never a standing
  cron.

## Phase 5 deliverable

This document and its checklist. **No code.** When — and only when — the ramp
gate is fully met and the owner signs off, `live_run.py` becomes a small,
reviewable diff against `paper_run.py`. Until then, the safest live-trading
code is the code that does not exist.
