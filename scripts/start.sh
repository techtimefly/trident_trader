#!/usr/bin/env bash
# Start all Trident Trader services (Postgres + Dashboard) via systemd.
# Also enables them so they auto-start on future boots.
#
# Shadow runner is managed separately by cron (09:20 ET on trading days).
# See scripts/run_shadow_scheduled.sh and crontab -l.
set -euo pipefail

echo "==> Enabling and starting Trident services..."
systemctl --user enable --now trident-postgres.service
systemctl --user enable --now trident-dashboard.service

echo ""
echo "==> Status:"
systemctl --user status trident-postgres.service trident-dashboard.service \
    --no-pager --lines=5

echo ""
LAN_IP="$(hostname -I | awk '{print $1}')"
echo "Dashboard: http://${LAN_IP}:8765/"
echo "           http://127.0.0.1:8765/"
echo ""
echo "Logs:  journalctl --user -u trident-dashboard -f"
echo "       journalctl --user -u trident-postgres  -f"
