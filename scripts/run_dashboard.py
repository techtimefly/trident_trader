"""Launch the FastAPI dashboard on http://127.0.0.1:8765.

Usage:
    PYTHONPATH=src python3 scripts/run_dashboard.py

Localhost only. For remote access, use Tailscale or SSH port-forwarding.
"""
from __future__ import annotations

import uvicorn


def main() -> None:
    uvicorn.run(
        "trident.dashboard.app:app",
        host="127.0.0.1",
        port=8765,
        log_level="info",
    )


if __name__ == "__main__":
    main()
