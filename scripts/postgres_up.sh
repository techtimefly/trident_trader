#!/usr/bin/env bash
# Bring up the Trident Postgres container and wait until it is ready.
# Called by trident-postgres.service ExecStart. Safe to run when already up.
#
# Uses docker directly instead of docker-compose to avoid docker-compose v1
# incompatibility with newer Docker Engine (missing ContainerConfig in inspect).
set -euo pipefail

PGDATA="/home/hal9000/agent/apps/trident_trader/trident_trader/pgdata"

if docker inspect trident-postgres >/dev/null 2>&1; then
    # Container exists — start it if stopped, no-op if already running.
    docker start trident-postgres >/dev/null 2>&1 || true
else
    # First run — create the container.
    docker run -d \
        --name trident-postgres \
        --restart unless-stopped \
        -e POSTGRES_USER=trident \
        -e POSTGRES_PASSWORD=trident \
        -e POSTGRES_DB=trident \
        -p 5432:5432 \
        -v "$PGDATA:/var/lib/postgresql/data" \
        postgres:16-alpine
fi

for _ in $(seq 1 30); do
    docker exec trident-postgres pg_isready -U trident -d trident \
        >/dev/null 2>&1 && exit 0
    sleep 2
done

echo "$(date -Is) trident-postgres did not become ready in 60s" >&2
exit 1
