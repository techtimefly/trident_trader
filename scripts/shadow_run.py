"""Run the ORB strategy against live Alpaca paper data, but DO NOT submit orders.

Every signal is logged + run through the risk gate; the gate decision is recorded
on both the audit log and the signals table. A heartbeat is written every 5s so
the dashboard knows the bot is alive. The kill switch (toggled from the dashboard)
is checked before every gate evaluation — when engaged, the gate rejects.

Usage:
    PYTHONPATH=src python3 scripts/shadow_run.py
"""
from __future__ import annotations

import asyncio
import signal as os_signal
from datetime import time as dtime
from decimal import Decimal

from trident.audit.log import configure_logging, get_logger, record
from trident.clock import is_market_open, now_et
from trident.data.bars import Bar, BarStore
from trident.data.feed import AlpacaBarFeed
from trident.data.persistence import persist_bar
from trident.persistence.models import Signal as SignalRow
from trident.persistence.session import session_scope
from trident.persistence.state import kill_switch_engaged, write_heartbeat
from trident.risk.gate import AccountState, MarketState, evaluate
from trident.risk.limits import RiskLimits
from trident.settings import get_settings
from trident.strategies.orb import OpeningRangeBreakout

WATCHLIST = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMD"]
HEARTBEAT_INTERVAL_SECONDS = 5


async def main() -> None:
    configure_logging()
    log = get_logger("shadow")
    settings = get_settings()
    if not settings.alpaca_api_key:
        log.error("missing_alpaca_credentials")
        return

    store = BarStore()
    strategy = OpeningRangeBreakout(symbols=WATCHLIST)
    limits = RiskLimits(
        risk_per_trade_pct=settings.risk_per_trade_pct,
        daily_loss_limit_pct=settings.daily_loss_limit_pct,
        max_concurrent_positions=settings.max_concurrent_positions,
    )

    starting_equity = await _fetch_starting_equity(settings)
    log.info("shadow_run_start", watchlist=WATCHLIST, starting_equity=str(starting_equity))

    async def on_bar(bar: Bar) -> None:
        try:
            persist_bar(bar)
        except Exception:
            log.exception("persist_bar_failed", symbol=bar.symbol)

        sig = strategy.on_bar(bar, store)
        if sig is None:
            return

        et = now_et()
        account = AccountState(
            equity=starting_equity,
            starting_equity_today=starting_equity,
            buying_power=starting_equity * Decimal("2"),
            open_positions={},
        )
        market = MarketState(kill_switch_active=kill_switch_engaged())
        decision = evaluate(sig, account, market, limits, dtime(et.hour, et.minute))

        # Persist signal + decision together. The audit log entry is the canonical record;
        # the signals row is for fast queries from the dashboard.
        with session_scope() as s:
            s.add(
                SignalRow(
                    ts=sig.ts,
                    strategy=sig.strategy,
                    symbol=sig.symbol,
                    side=sig.side,
                    entry_price=sig.entry_price,
                    stop_price=sig.stop_price,
                    target_price=sig.target_price,
                    meta=sig.meta,
                    gate_decision="approved" if decision.approved else "rejected",
                    gate_reason=decision.reason,
                )
            )

        record(
            "signal_generated",
            actor=sig.strategy,
            payload={
                "symbol": sig.symbol,
                "side": sig.side,
                "entry": str(sig.entry_price),
                "stop": str(sig.stop_price),
                "target": str(sig.target_price),
                "meta": sig.meta,
            },
        )
        record(
            "gate_decision",
            actor="risk_gate",
            payload={
                "symbol": sig.symbol,
                "approved": decision.approved,
                "reason": decision.reason,
                "detail": decision.detail,
                "shares": decision.shares,
            },
        )
        if decision.approved:
            log.info(
                "would_submit_order_shadow",
                symbol=sig.symbol,
                shares=decision.shares,
                entry=str(sig.entry_price),
                stop=str(sig.stop_price),
                target=str(sig.target_price),
            )
        else:
            log.info("rejected_in_shadow", symbol=sig.symbol, reason=decision.reason)

    feed = AlpacaBarFeed(
        api_key=settings.alpaca_api_key,
        api_secret=settings.alpaca_api_secret,
        symbols=WATCHLIST,
        store=store,
        feed=settings.alpaca_data_feed,
    )
    feed.on_bar(on_bar)

    stop_event = asyncio.Event()

    def _stop(*_: object) -> None:
        log.info("shadow_run_stop_signal")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig_name in (os_signal.SIGINT, os_signal.SIGTERM):
        loop.add_signal_handler(sig_name, _stop)

    async def heartbeat_loop() -> None:
        while not stop_event.is_set():
            try:
                write_heartbeat()
            except Exception:
                log.exception("heartbeat_failed")
            try:
                await asyncio.wait_for(stop_event.wait(), HEARTBEAT_INTERVAL_SECONDS)
            except asyncio.TimeoutError:
                pass

    feed_task = asyncio.create_task(feed.run())
    hb_task = asyncio.create_task(heartbeat_loop())

    await stop_event.wait()
    feed_task.cancel()
    hb_task.cancel()
    log.info("shadow_run_complete")


async def _fetch_starting_equity(settings) -> Decimal:  # type: ignore[no-untyped-def]
    if settings.account_equity_override:
        return Decimal(settings.account_equity_override)
    try:
        from alpaca.trading.client import TradingClient

        client = TradingClient(
            api_key=settings.alpaca_api_key,
            secret_key=settings.alpaca_api_secret,
            paper=True,
        )
        return Decimal(str(client.get_account().equity))
    except Exception:
        return Decimal("100000")


if __name__ == "__main__":
    _ = is_market_open()
    asyncio.run(main())
