"""Read-only Alpaca REST wrapper used by the dashboard.

We never call the trading endpoints from the dashboard process. This wrapper is
strictly: get account, list positions. Failures are swallowed and returned as None
so a flaky network doesn't take the page down.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from trident.audit.log import get_logger
from trident.settings import get_settings

log = get_logger("dashboard.alpaca")


@dataclass(frozen=True)
class AccountView:
    equity: Decimal
    cash: Decimal
    buying_power: Decimal
    status: str
    pattern_day_trader: bool
    daytrade_count: int


@dataclass(frozen=True)
class PositionView:
    symbol: str
    qty: int
    avg_entry_price: Decimal
    market_value: Decimal
    unrealized_pl: Decimal
    unrealized_plpc: Decimal
    current_price: Decimal
    side: str


def _client() -> Any:
    settings = get_settings()
    from alpaca.trading.client import TradingClient

    return TradingClient(
        api_key=settings.alpaca_api_key,
        secret_key=settings.alpaca_api_secret,
        paper=True,
    )


def get_account() -> AccountView | None:
    try:
        acc = _client().get_account()
        return AccountView(
            equity=Decimal(str(acc.equity)),
            cash=Decimal(str(acc.cash)),
            buying_power=Decimal(str(acc.buying_power)),
            status=str(acc.status).split(".")[-1],
            pattern_day_trader=bool(acc.pattern_day_trader),
            daytrade_count=int(acc.daytrade_count),
        )
    except Exception:
        log.exception("alpaca_account_fetch_failed")
        return None


def list_positions() -> list[PositionView]:
    try:
        rows = _client().get_all_positions()
        out: list[PositionView] = []
        for p in rows:
            out.append(
                PositionView(
                    symbol=str(p.symbol),
                    qty=int(float(p.qty)),
                    avg_entry_price=Decimal(str(p.avg_entry_price)),
                    market_value=Decimal(str(p.market_value)),
                    unrealized_pl=Decimal(str(p.unrealized_pl)),
                    unrealized_plpc=Decimal(str(p.unrealized_plpc)) * Decimal("100"),
                    current_price=Decimal(str(p.current_price)),
                    side=str(p.side).split(".")[-1].lower(),
                )
            )
        return out
    except Exception:
        log.exception("alpaca_positions_fetch_failed")
        return []
