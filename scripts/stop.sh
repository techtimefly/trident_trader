#!/usr/bin/env bash
# Stop Trident Trader services.
# Pass --postgres to also stop the Postgres container (default: leave it running).
set -euo pipefail

STOP_POSTGRES=0
for arg in "$@"; do
    [[ "$arg" == "--postgres" ]] && STOP_POSTGRES=1
done

echo "==> Stopping dashboard..."
systemctl --user stop trident-dashboard.service

if [[ "$STOP_POSTGRES" -eq 1 ]]; then
    echo "==> Stopping Postgres..."
    systemctl --user stop trident-postgres.service
else
    echo "(Postgres left running — pass --postgres to stop it too)"
fi

echo "Done."
