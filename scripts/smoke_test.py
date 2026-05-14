"""Sanity-check that Alpaca paper credentials work and the DB is reachable.

Run after setting ALPACA_API_KEY / ALPACA_API_SECRET in .env and after
`docker compose up -d` to start Postgres. Does NOT submit any orders.

Usage:
    PYTHONPATH=src python3 scripts/smoke_test.py
"""
from __future__ import annotations

import sys

from sqlalchemy import text

from trident.audit.log import configure_logging, get_logger
from trident.persistence.session import get_engine
from trident.settings import get_settings


def check_db() -> bool:
    log = get_logger("smoke.db")
    try:
        with get_engine().connect() as conn:
            row = conn.execute(text("select 1")).scalar_one()
            assert row == 1
        log.info("db_ok")
        return True
    except Exception as exc:
        log.error("db_failed", error=str(exc))
        return False


def check_alpaca() -> bool:
    log = get_logger("smoke.alpaca")
    settings = get_settings()
    if not settings.alpaca_api_key or not settings.alpaca_api_secret:
        log.error("alpaca_no_credentials")
        return False
    if not settings.is_paper:
        log.error("alpaca_not_paper_url", url=settings.alpaca_base_url)
        return False
    try:
        from alpaca.trading.client import TradingClient

        client = TradingClient(
            api_key=settings.alpaca_api_key,
            secret_key=settings.alpaca_api_secret,
            paper=True,
        )
        account = client.get_account()
        log.info(
            "alpaca_ok",
            status=str(account.status),
            equity=str(account.equity),
            buying_power=str(account.buying_power),
            currency=str(account.currency),
        )
        return True
    except Exception as exc:
        log.error("alpaca_failed", error=str(exc))
        return False


def main() -> int:
    configure_logging()
    log = get_logger("smoke")
    log.info("smoke_test_start")
    db_ok = check_db()
    alpaca_ok = check_alpaca()
    log.info("smoke_test_complete", db_ok=db_ok, alpaca_ok=alpaca_ok)
    return 0 if (db_ok and alpaca_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
