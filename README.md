# trident_trader

Personal automated day-trading bot. Paper trading only until it has earned trust.

This repo is intentionally minimal — one strategy, one broker, one user. The goal
is correctness and discipline, not feature breadth.

> **Not investment advice.** This is software for the author's own learning and
> experimentation. Trading securities involves risk of loss.

## Current scope

- **Strategies:** selected by name from a registry — `orb_5m` (Opening Range
  Breakout) and `vwap_reversion` (VWAP mean-reversion, long + short). Traded on a
  DB-backed watchlist. Multiple named watchlists are supported; exactly one is
  active at a time, and the runner trades that one. With none set, the watchlist
  falls back to liquid US large-caps (SPY, QQQ, AAPL, MSFT, NVDA, AMD).
- **Broker:** Alpaca paper account (the adapter refuses non-paper URLs).
- **Data:** Alpaca's bundled IEX feed.
- **Modes:**
  - `shadow_run.py` — signals generated, gate evaluated, **no orders submitted**.
  - `paper_run.py` — submits bracket orders to the Alpaca paper account,
    polls fills, reconciles positions, flattens at EOD.
  - `deadman.py` — independent watchdog process that flattens if the runner's
    heartbeat goes stale.

## Setup

### 1. Alpaca paper account

1. Sign up at <https://alpaca.markets> (free).
2. In the dashboard, toggle to **Paper Trading** (top right).
3. Click **View / Generate API Keys** in the right panel.
4. Copy the **Key ID** and **Secret**.

### 2. Local environment

```bash
git clone <this repo>
cd trident_trader

cp .env.example .env
# Edit .env and paste your ALPACA_API_KEY and ALPACA_API_SECRET.

python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
# This installs the `trident` CLI on PATH. After this you can run
# `trident <command>` as a shorthand for the scripts (see below).
```

### 3. Postgres

```bash
docker compose up -d postgres
alembic upgrade head
```

For persistent auto-start on boot, two systemd user services
(`trident-postgres.service` and `trident-dashboard.service`) can be enabled:

```bash
scripts/start.sh          # enable + start both
scripts/stop.sh           # stop dashboard (add --postgres to also stop Postgres)
scripts/postgres_up.sh    # create-or-start the container and wait for pg_isready
```

The services and `postgres_up.sh` use `docker` directly (not `docker compose`)
to avoid docker-compose v1 compatibility issues.

### 4. Smoke test

```bash
PYTHONPATH=src python scripts/smoke_test.py
# or equivalently:
trident smoke
```

Expected output (JSON lines): `db_ok`, `alpaca_ok`. If either fails, fix that
before going further.

### 5. Backfill daily bars

```bash
PYTHONPATH=src python scripts/backfill_daily.py 60
```

### 6. Shadow run (no orders submitted)

```bash
PYTHONPATH=src python scripts/shadow_run.py
# or equivalently:
trident run shadow
```

This connects to Alpaca's WebSocket bar feed during US market hours, runs the
ORB strategy, and logs every signal + risk-gate decision. **No orders are placed.**
The runner writes a heartbeat every 5 seconds so the dashboard can show whether
it's alive.

To run it automatically each trading day, install the cron launcher:

```bash
crontab -e
# Add one line. The time must be in server-local time, not ET:
# - If the server is in a UTC-offset timezone, compute accordingly.
# - On this machine (MDT, UTC-6): 07:20 local = 09:20 ET year-round.
# - The Debian vixie-cron used here ignores CRON_TZ for scheduling;
#   set the variable anyway so child processes see the correct TZ.
#   CRON_TZ=America/New_York
#   20 7 * * 1-5 /absolute/path/to/scripts/run_shadow_scheduled.sh >> logs/cron.log 2>&1
```

`run_shadow_scheduled.sh` guards against weekends, NYSE holidays, duplicate
starts, and Postgres being down. It runs `shadow_run.py --strategy orb_5m` for
up to 7 hours, then exits cleanly via SIGTERM.

### 6a. Replay against historical days (no waiting for the open)

```bash
PYTHONPATH=src python scripts/replay.py                  # yesterday
PYTHONPATH=src python scripts/replay.py --date 2026-05-12
PYTHONPATH=src python scripts/replay.py --days 250       # last ~year
PYTHONPATH=src python scripts/replay.py --days 90 --no-persist   # console-only
# or equivalently:
trident replay
trident replay --date 2026-05-12
trident replay --days 250
trident replay --days 90 --no-persist
```

Fetches 1-min IEX bars for the chosen day(s), feeds them through the same
strategy + risk gate that the live runner uses, then simulates fills against
the rest of that day. Prints a per-trade table to the console **and** writes
the run + every trade to the database. The dashboard's "Latest replay" panel
shows the most recent run's summary + trade list.

This is **not** a real backtest — fills are idealistic (entry at the breakout
bar's close, exits at exact stop or target). It's for sanity-checking the
strategy on recent data and getting comfortable with the output shape before
the next session opens. The honest backtest harness (slippage, fees,
walk-forward) is `scripts/backtest.py`.

### 6b. Paper run (real bracket orders to the paper account)

> Use this only after a week or more of clean shadow runs. The two scripts
> share all logic except whether orders are actually submitted.

```bash
# Terminal A — the runner
PYTHONPATH=src python scripts/paper_run.py
# or equivalently:
trident run paper

# Terminal B — the dead-man's switch (independent process)
PYTHONPATH=src python scripts/deadman.py
```

What the paper runner does, on top of what shadow does:
- Submits approved signals as **bracket orders** with deterministic
  `client_order_id`s (signal-id-derived, so retries can never double-submit).
- Polls Alpaca every 10s to update local order state.
- Reconciles local positions against Alpaca every 60s; drift is audited.
- Schedules an **EOD flatten** 5 minutes before the session close
  (`close_all_positions(cancel_orders=True)`). Half-day sessions are
  handled (12:55 ET on the early-close day after Thanksgiving, etc.).

The dead-man's switch is a separate process that watches the heartbeat.
If the runner stops writing a heartbeat for >45 seconds **and** there are
open positions or orders, it cancels everything and closes everything.
Run it in a second terminal, tmux pane, or systemd unit.

The dashboard's **Orders** panel shows today's orders by state. The kill
switch in the hero strip works in both shadow and paper modes — engaging
it makes the gate reject every new signal until you release it.

### 7. Dashboard

In a second terminal:

```bash
PYTHONPATH=src python scripts/run_dashboard.py
# or equivalently:
trident run dashboard
```

Open <http://127.0.0.1:8765>. The dashboard has five pages reachable from the
top navigation bar. A persistent status strip on every page shows equity,
market state, bot heartbeat, and the kill switch.

- **Trading** — open positions, today's signals with gate decisions, today's
  orders, and manual controls (close a position, adjust a stop, cancel an order).
- **Plan** — today's capital-budget and day-trade cap; watchlist management.
  Every named watchlist shows live per-symbol quotes (last price, day change,
  bid/ask, volume from the IEX snapshot feed); add/remove symbols,
  create/rename/activate/delete lists. The runner trades the active list.
- **Screener** — managed screen-filter presets (editable criteria, activate /
  delete); latest screen results with an "add to watchlist" action; backtest
  overlay panel showing how screener symbols have performed in replay runs; AI
  stock suggestions panel with a **Run pre-market check** button. AI suggestions
  require `ANTHROPIC_API_KEY` or `CLAUDE_CODE_OAUTH_TOKEN`; degrades gracefully
  if neither is set.
- **Research** — per-trade P&L for closed live trades; per-symbol aggregated
  stats (trades, win rate, avg R, total P&L) across all runs; signal history
  browser (by date, with fill prices); latest replay run summary and trade list;
  backtest history (all runs, newest-first) with a trigger form for new backtest
  runs; strategy comparison panel.
- **System** — settings panel (API keys, risk defaults, operational config,
  backtest cost model — edits `.env` in-place); connection health (Alpaca, FMP,
  DB); live log tail; last 24 hours of audit events.

The kill switch on the status strip engages immediately; the gate rejects every
new signal until it is released. The toggle is persisted to the `system_state`
table so runners see it without a restart.

The dashboard has no auth. The launcher binds to `0.0.0.0` by default so
Codespaces / Docker port-forwarding works; on a personal machine set
`DASHBOARD_HOST=127.0.0.1` to keep it off your LAN. Either way, do not expose it
to the public internet — to reach it from another device, use Tailscale
(`tailscale serve`) or SSH port-forwarding.

### Tail logs / query the DB

```bash
tail -f logs/*.log | jq

psql postgresql://trident:trident@localhost:5432/trident -c \
  "select ts, event_type, payload from audit_events order by ts desc limit 50;"
```

## Running tests

```bash
pytest -q
```

The unit tests cover the risk gate, position sizing, the market clock, the bar
store, and the ORB strategy. They run without a database or network.

## `trident` CLI

After `pip install -e ".[dev]"` the `trident` command is on PATH as a shorthand
for every operation. The underlying `scripts/*.py` files are unchanged — both
forms work.

```
trident run shadow [--strategy S]       # live data, no orders
trident run paper  [--strategy S] [-y]  # paper orders + deadman reminder
trident run dashboard                   # FastAPI on :8765

trident replay   [--date D] [--days N] [--equity N] [--strategy S] [--no-persist]
trident backtest [--date D] [--days N] [--window-days N] [--slippage-bps N] ...
trident compare  [--date D] [--days N] [--strategy S ...]
trident screen   [--preset N] [--min-price N] [--max-price N] [--min-change N] ...
trident suggest  [--max N]

trident smoke                           # DB + Alpaca credential check
trident status                          # DB / Alpaca / kill switch / watchlist / heartbeat

trident kill-switch on|off              # toggle without the dashboard
trident watchlist                       # list all named watchlists
trident watchlist add AAPL NVDA
trident watchlist remove AAPL
trident watchlist activate NAME
trident watchlist create NAME [SYMS...]
trident watchlist delete NAME

trident db upgrade                      # alembic upgrade head
```

## Project layout

```
src/trident/
  settings.py          # pydantic-settings, loads .env
  clock.py             # market hours / holidays / early closes
  watchlist.py         # WATCHLIST constant + DB-backed resolve_watchlist()
  cli.py               # `trident` CLI entry point (Typer); wraps scripts + package functions
  data/                # WebSocket feed + bar store + bar persistence + backfill
  strategies/          # Strategy protocol, registry, ORB + VWAP-reversion, management
  risk/                # the pre-trade gate, sizing, and limits
  execution/           # Broker protocol + Alpaca adapter + bracket/single-leg orders
  portfolio/           # order tracking + position reconciliation + management
  accounting/          # pure round-trip + wash-sale computation
  screener/            # stock-screener: criteria, engine, FMP universe layer,
                       #   managed presets (fmp.py, presets.py, data.py, …)
  suggest/             # AI pre-market stock suggestions
  safety/              # EOD flatten
  backtest/            # fill simulator + honest backtest + strategy comparison
  audit/               # append-only event log + structured logging
  persistence/         # SQLAlchemy models + migrations + kill switch state +
                       #   screen_presets_store.py, watchlist_store.py, …
  dashboard/           # FastAPI + HTMX dashboard (localhost only)
tests/unit/            # pure-function tests for the safety-critical code
scripts/               # smoke_test, shadow_run, replay, backtest, compare,
                       # paper_run, deadman, run_dashboard, backfill_daily,
                       # screen, suggest,
                       # postgres_up.sh (create-or-start the Docker container),
                       # run_shadow_scheduled.sh (cron launcher, 09:20 ET weekdays),
                       # start.sh / stop.sh (systemd service helpers)
```

## Roadmap

- **v0.1:** scaffolding, market clock, data feed, strategy, risk gate,
  dashboard with kill switch.
- **v0.2:** Alpaca execution adapter, bracket orders with
  idempotency keys, polling-based order tracking, reconciliation loop,
  dead-man's switch, EOD flatten, paper_run.py.
- **Phases 1–5 (current):** pluggable strategy registry + comparison tooling,
  a VWAP mean-reversion strategy, a DB-backed dynamic watchlist, active
  position management, per-trade P&L + wash-sale accounting, crash recovery,
  and an honest backtest harness (slippage, fees, walk-forward). See
  `docs/ROADMAP.md` and `docs/ROADMAP_PROGRESS.md`.
- **v0.3:** Trade-updates WebSocket (replace polling), partial-fill handling.
- **v0.4:** LLM-narrated trade journal (Claude or GPT, one model, cached).
- **Some day:** Real money. After at least 8 consecutive weeks of paper
  profitability across at least one regime change, **and** a clean audit-log
  review by the author.

## Operating principles

1. **No position without a stop.** The bracket order is non-negotiable; v0.1
   shadow logs the entry, stop, and target as a single intent.
2. **The risk gate is the most important code.** Treat it accordingly: pure
   function, exhaustive unit tests, change carefully.
3. **Audit everything.** Every signal, every gate decision, every order state
   change. You will be reading these logs at 11 PM trying to understand
   yesterday's loss.
4. **Paper, then shadow against live, then paper-execute, then real.** Skip none
   of these stages.
