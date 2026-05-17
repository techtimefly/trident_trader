# trident_trader

Personal automated day-trading bot. Paper trading only until it has earned trust.

This repo is intentionally minimal — one strategy, one broker, one user. The goal
is correctness and discipline, not feature breadth.

> **Not investment advice.** This is software for the author's own learning and
> experimentation. Trading securities involves risk of loss.

## Current scope

- **Strategy:** 5-minute Opening Range Breakout on liquid US large-caps
  (SPY, QQQ, AAPL, MSFT, NVDA, AMD).
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
```

### 3. Postgres

```bash
docker compose up -d postgres
alembic upgrade head
```

### 4. Smoke test

```bash
PYTHONPATH=src python scripts/smoke_test.py
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
```

This connects to Alpaca's WebSocket bar feed during US market hours, runs the
ORB strategy, and logs every signal + risk-gate decision. **No orders are placed.**
The runner writes a heartbeat every 5 seconds so the dashboard can show whether
it's alive.

### 6a. Replay against historical days (no waiting for the open)

```bash
PYTHONPATH=src python scripts/replay.py                  # yesterday
PYTHONPATH=src python scripts/replay.py --date 2026-05-12
PYTHONPATH=src python scripts/replay.py --days 250       # last ~year
PYTHONPATH=src python scripts/replay.py --days 90 --no-persist   # console-only
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
walk-forward) lands in v0.3.

### 6b. Paper run (real bracket orders to the paper account)

> Use this only after a week or more of clean shadow runs. The two scripts
> share all logic except whether orders are actually submitted.

```bash
# Terminal A — the runner
PYTHONPATH=src python scripts/paper_run.py

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
```

Open <http://127.0.0.1:8765>. The page shows:
- Account equity, cash, buying power (live from Alpaca).
- Market open/closed indicator and a heartbeat-based bot status.
- Open positions in the Alpaca paper account.
- Today's signals with the gate decision next to each.
- The last 24 hours of audit events.
- A red **kill switch** button. Engaging it makes the gate reject every new
  signal until you release it. The toggle is persisted to the `system_state`
  table so the shadow runner sees it without needing a restart.

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

## Project layout

```
src/trident/
  settings.py          # pydantic-settings, loads .env
  clock.py             # market hours / holidays / early closes
  data/                # WebSocket feed + bar store + bar persistence
  strategies/          # Strategy protocol + ORB implementation
  risk/                # the pre-trade gate, sizing, and limits
  execution/           # Broker protocol + Alpaca adapter + bracket orders
  portfolio/           # order tracking + position reconciliation
  safety/              # EOD flatten
  backtest/            # idealistic fill simulator used by replay
  audit/               # append-only event log + structured logging
  persistence/         # SQLAlchemy models + migrations + kill switch state
  dashboard/           # FastAPI + HTMX dashboard (localhost only)
tests/unit/            # pure-function tests for the safety-critical code
scripts/               # smoke_test, shadow_run, replay, paper_run, deadman,
                       # run_dashboard, backfill_daily
```

## Roadmap

- **v0.1:** scaffolding, market clock, data feed, strategy, risk gate,
  dashboard with kill switch.
- **v0.2 (current):** Alpaca execution adapter, bracket orders with
  idempotency keys, polling-based order tracking, reconciliation loop,
  dead-man's switch, EOD flatten, paper_run.py.
- **v0.3:** Trade-updates WebSocket (replace polling), partial-fill
  handling, scale-out at 1R + trailing remainder, backtest harness with
  honest slippage + walk-forward.
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
