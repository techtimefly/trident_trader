#!/usr/bin/env bash
# Launches the Trident shadow runner for one trading session.
#
# Designed to be invoked by cron ~10 minutes before the market open. It self-
# terminates after the close, so each trading day gets exactly one clean run.
# Shadow mode only: live data, signals + risk gate evaluated, NEVER submits orders.
#
# Cron entry (install with `crontab -e`):
#   CRON_TZ=America/New_York
#   20 9 * * 1-5 /home/hal9000/agent/apps/trident_trader/trident_trader/scripts/run_shadow_scheduled.sh >> /home/hal9000/agent/apps/trident_trader/trident_trader/logs/cron.log 2>&1
#
# Credentials: the app loads ALPACA_API_KEY / ALPACA_API_SECRET from the project
# .env file. cron does NOT inherit an interactive shell, so .env must exist.
set -euo pipefail

PROJECT_DIR="/home/hal9000/agent/apps/trident_trader/trident_trader"
export PATH="/usr/local/bin:/usr/bin:/bin:${PATH:-}"
cd "$PROJECT_DIR"

PY="$PROJECT_DIR/.venv/bin/python"
LOG_DIR="$PROJECT_DIR/logs"; mkdir -p "$LOG_DIR"
STAMP="$(date +%Y%m%d)"

# 1. Trading-day guard — skip weekends + NYSE holidays (reuses trident.clock).
if ! PYTHONPATH=src "$PY" -c "import sys; from datetime import date; \
from trident.clock import is_trading_day; \
sys.exit(0 if is_trading_day(date.today()) else 1)"; then
  echo "$(date -Is) not a trading day — skip" >> "$LOG_DIR/scheduler.log"
  exit 0
fi

# 2. Don't start a second runner if one is already alive (manual + cron collision).
if pgrep -f "scripts/shadow_run.py" >/dev/null 2>&1; then
  echo "$(date -Is) shadow_run already running — skip" >> "$LOG_DIR/scheduler.log"
  exit 0
fi

# 3. Ensure Postgres is up. trident-postgres.service (systemd) handles this on
#    boot, but cron may fire before the service is active, so we verify and start
#    the container directly via docker if needed.
"$PROJECT_DIR/scripts/postgres_up.sh"

# 4. Run the shadow runner for one session. SIGTERM after ~7h (covers 09:30-16:00
#    ET + margin); shadow_run.py shuts the feed down cleanly and exits within a
#    few seconds. --kill-after is a safety net: if shutdown ever wedges, SIGKILL
#    follows 30s later so a stuck process cannot block the next day via guard #2.
#    A hard kill is safe in shadow mode anyway — no positions or orders exist.
echo "$(date -Is) starting shadow_run" >> "$LOG_DIR/scheduler.log"
PYTHONPATH=src timeout --signal=TERM --kill-after=30s 7h "$PY" \
  scripts/shadow_run.py --strategy orb_5m \
  >> "$LOG_DIR/shadow-$STAMP.log" 2>&1 || true
echo "$(date -Is) shadow_run exited" >> "$LOG_DIR/scheduler.log"
