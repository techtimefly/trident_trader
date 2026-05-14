# trident_trader

Personal automated day-trading bot. Paper trading only until it has earned trust.

This repo is intentionally minimal — one strategy, one broker, one user. The goal
is correctness and discipline, not feature breadth.

> **Not investment advice.** This is software for the author's own learning and
> experimentation. Trading securities involves risk of loss.

## v0.1 scope

- **Strategy:** 5-minute Opening Range Breakout on liquid US large-caps
  (SPY, QQQ, AAPL, MSFT, NVDA, AMD).
- **Broker:** Alpaca paper account.
- **Data:** Alpaca's bundled IEX feed.
- **Mode:** Shadow only — signals generated, gate evaluated, no orders submitted.
  Live paper execution lands in v0.2 after a clean week of shadow runs.

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

### 6. Shadow run

```bash
PYTHONPATH=src python scripts/shadow_run.py
```

This connects to Alpaca's WebSocket bar feed during US market hours, runs the
ORB strategy, and logs every signal + risk-gate decision. **No orders are placed.**
The runner writes a heartbeat every 5 seconds so the dashboard can show whether
it's alive.

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

The dashboard binds to `127.0.0.1` only. To access from another device, use
Tailscale (`tailscale serve`) or SSH port-forwarding — do not expose it to the
public internet.

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
  audit/               # append-only event log + structured logging
  persistence/         # SQLAlchemy models + migrations + kill switch state
  dashboard/           # FastAPI + HTMX dashboard (localhost only)
tests/unit/            # pure-function tests for the safety-critical code
scripts/               # smoke_test, shadow_run, run_dashboard, backfill_daily
```

## Roadmap

- **v0.1 (this commit):** scaffolding, market clock, data feed, strategy, risk
  gate, dashboard with kill switch.
- **v0.2:** Alpaca execution adapter, bracket orders with idempotency keys,
  reconciliation loop, dead-man's switch, EOD flatten.
- **v0.3:** Backtest harness with honest slippage + walk-forward.
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
