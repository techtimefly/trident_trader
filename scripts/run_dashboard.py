"""Launch the FastAPI dashboard.

Usage:
    PYTHONPATH=src python3 scripts/run_dashboard.py
    PYTHONPATH=src DASHBOARD_HOST=127.0.0.1 python3 scripts/run_dashboard.py

Default host is 0.0.0.0 so Codespaces / Docker port-forwarding works. If you're
running on a personal machine and don't want the dashboard reachable from your
LAN, set DASHBOARD_HOST=127.0.0.1 (or run behind Tailscale).
"""
from __future__ import annotations

import os

import uvicorn


def main() -> None:
    host = os.environ.get("DASHBOARD_HOST", "0.0.0.0")
    port = int(os.environ.get("DASHBOARD_PORT", "8765"))
    uvicorn.run(
        "trident.dashboard.app:app",
        host=host,
        port=port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
