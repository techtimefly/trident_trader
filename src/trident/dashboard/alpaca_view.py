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


@dataclass(frozen=True)
class QuoteView:
    """A latest-price snapshot for one symbol. IEX feed on the free tier."""

    symbol: str
    last: Decimal
    change: Decimal | None  # last - previous daily close
    change_pct: Decimal | None  # percent, quantized to 0.01
    bid: Decimal | None
    ask: Decimal | None
    volume: Decimal | None  # daily-bar volume


def _client() -> Any:
    settings = get_settings()
    from alpaca.trading.client import TradingClient

    return TradingClient(
        api_key=settings.alpaca_api_key,
        secret_key=settings.alpaca_api_secret,
        paper=True,
    )


def _data_client() -> Any:
    """Historical/snapshot data client — same recipe as ``screener/data.py``."""
    settings = get_settings()
    from alpaca.data.historical import StockHistoricalDataClient

    return StockHistoricalDataClient(settings.alpaca_api_key, settings.alpaca_api_secret)


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


def get_quotes(symbols: list[str]) -> dict[str, QuoteView]:
    """Fetch latest-price snapshots for ``symbols``. Outer-ring: never raises.

    Returns a ``{symbol: QuoteView}`` map. Symbols with no usable data are
    omitted; a total failure (network, auth) returns an empty dict. Prices
    reflect the IEX feed on the free tier.
    """
    if not symbols:
        return {}
    try:
        from alpaca.data.enums import DataFeed
        from alpaca.data.requests import StockSnapshotRequest

        settings = get_settings()
        feed = DataFeed.IEX if settings.alpaca_data_feed.lower() == "iex" else DataFeed.SIP
        req = StockSnapshotRequest(symbol_or_symbols=list(symbols), feed=feed)
        snapshots = _data_client().get_stock_snapshot(req)
    except Exception:
        log.exception("alpaca_quotes_fetch_failed")
        return {}

    out: dict[str, QuoteView] = {}
    for sym in symbols:
        snap = snapshots.get(sym)
        if snap is None:
            continue
        try:
            trade = getattr(snap, "latest_trade", None)
            daily = getattr(snap, "daily_bar", None)
            prev = getattr(snap, "previous_daily_bar", None)
            quote = getattr(snap, "latest_quote", None)

            # Last price: prefer the latest IEX trade; fall back to today's
            # daily close when there are no prints yet (pre-market, thin name).
            last: Decimal | None = None
            if trade is not None and trade.price is not None:
                last = Decimal(str(trade.price))
            elif daily is not None and daily.close is not None:
                last = Decimal(str(daily.close))
            if last is None or last <= 0:
                continue

            prev_close: Decimal | None = None
            if prev is not None and prev.close is not None:
                prev_close = Decimal(str(prev.close))

            change: Decimal | None = None
            change_pct: Decimal | None = None
            if prev_close is not None and prev_close > 0:
                change = last - prev_close
                change_pct = (change / prev_close * Decimal("100")).quantize(Decimal("0.01"))

            bid: Decimal | None = None
            ask: Decimal | None = None
            if quote is not None:
                if quote.bid_price is not None and quote.bid_price > 0:
                    bid = Decimal(str(quote.bid_price))
                if quote.ask_price is not None and quote.ask_price > 0:
                    ask = Decimal(str(quote.ask_price))

            volume: Decimal | None = None
            if daily is not None and daily.volume is not None:
                volume = Decimal(str(daily.volume))

            out[sym] = QuoteView(
                symbol=sym,
                last=last,
                change=change,
                change_pct=change_pct,
                bid=bid,
                ask=ask,
                volume=volume,
            )
        except (ArithmeticError, ValueError, AttributeError):
            log.exception("alpaca_quote_parse_failed", symbol=sym)
            continue
    return out
