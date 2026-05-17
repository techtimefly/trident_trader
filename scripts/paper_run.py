"""Live shadow + paper execution runner.

Identical loop to shadow_run.py but:
  - Approved signals are submitted to Alpaca as bracket orders.
  - Orders are polled every 10s; state changes update the local DB + audit log.
  - Positions are reconciled against Alpaca every 60s.
  - EOD flatten fires 5 minutes before the session close.

The file name is intentional: paper_run.py means we are paper-executing. There is
no live_run.py until v0.3 at the earliest, and only after ≥8 weeks of clean paper.

Usage:
    PYTHONPATH=src python3 scripts/paper_run.py
"""
from __future__ import annotations

import asyncio
import signal as os_signal
from dataclasses import replace
from datetime import UTC, datetime
from datetime import time as dtime
from decimal import Decimal

from trident.audit.log import configure_logging, get_logger, record
from trident.clock import is_market_open, now_et
from trident.data.bars import Bar, BarStore
from trident.data.feed import AlpacaBarFeed
from trident.data.persistence import persist_bar
from trident.execution.alpaca import AlpacaBroker
from trident.execution.orders import build_bracket
from trident.persistence.daily_plan import resolve_today
from trident.persistence.models import Signal as SignalRow
from trident.persistence.session import session_scope
from trident.persistence.state import kill_switch_engaged, write_heartbeat
from trident.portfolio.tracking import reconcile_positions, sync_orders
from trident.risk.gate import AccountState, MarketState, evaluate
from trident.risk.limits import RiskLimits
from trident.safety.eod_flatten import flatten_now, seconds_until_flatten
from trident.settings import get_settings
from trident.strategies.orb import OpeningRangeBreakout

WATCHLIST = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMD"]

HEARTBEAT_INTERVAL_SECONDS = 5
ORDER_POLL_SECONDS = 10
RECONCILE_SECONDS = 60
SESSION_START_ISO = ""  # filled in at startup; we list orders/fills after this point


async def main() -> None:
    configure_logging()
    log = get_logger("paper_run")
    settings = get_settings()
    if not settings.alpaca_api_key:
        log.error("missing_alpaca_credentials")
        return
    if not settings.is_paper:
        log.error("refusing_non_paper_base_url", url=settings.alpaca_base_url)
        return

    broker = AlpacaBroker()
    store = BarStore()
    strategy = OpeningRangeBreakout(symbols=WATCHLIST)
    limits = RiskLimits(
        risk_per_trade_pct=settings.risk_per_trade_pct,
        daily_loss_limit_pct=settings.daily_loss_limit_pct,
        max_concurrent_positions=settings.max_concurrent_positions,
    )

    starting_equity = await _fetch_starting_equity(broker, settings)
    session_start_iso = datetime.now(UTC).isoformat()
    log.info(
        "paper_run_start",
        watchlist=WATCHLIST,
        starting_equity=str(starting_equity),
        session_start=session_start_iso,
    )
    record(
        "runner_start",
        actor="paper_run",
        payload={"watchlist": WATCHLIST, "starting_equity": str(starting_equity)},
    )

    stop_event = asyncio.Event()

    def _stop(*_: object) -> None:
        log.info("paper_run_stop_signal")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig_name in (os_signal.SIGINT, os_signal.SIGTERM):
        loop.add_signal_handler(sig_name, _stop)

    async def on_bar(bar: Bar) -> None:
        try:
            persist_bar(bar)
        except Exception:
            log.exception("persist_bar_failed", symbol=bar.symbol)

        sig = strategy.on_bar(bar, store)
        if sig is None:
            return

        et = now_et()
        # Daily Plan: read today's caps + observed facts. A DB failure here is
        # reject-on-doubt — skip the signal rather than trade outside the plan.
        try:
            plan_ctx = resolve_today(et.date())
        except Exception:
            log.exception("daily_plan_fact_query_failed", symbol=sig.symbol)
            return
        per_bar_limits = replace(
            limits,
            daily_budget_pct=plan_ctx.budget_pct,
            max_day_trades=plan_ctx.max_day_trades,
        )
        broker_positions = {p.symbol: p.qty for p in broker.list_positions()}
        # Equity for sizing is the paper account's current equity.
        equity = await _fetch_starting_equity(broker, settings)
        account = AccountState(
            equity=equity,
            starting_equity_today=starting_equity,
            buying_power=equity * Decimal("2"),
            open_positions=broker_positions,
            notional_deployed_today=plan_ctx.notional_deployed_today,
            day_trades_in_window=plan_ctx.day_trades_in_window,
        )
        market = MarketState(kill_switch_active=kill_switch_engaged())
        decision = evaluate(sig, account, market, per_bar_limits, dtime(et.hour, et.minute))

        signal_id = None
        with session_scope() as s:
            row = SignalRow(
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
            s.add(row)
            s.flush()
            signal_id = row.id

        record(
            "signal_generated",
            actor=sig.strategy,
            payload={
                "signal_id": str(signal_id),
                "symbol": sig.symbol,
                "side": sig.side,
                "entry": str(sig.entry_price),
                "stop": str(sig.stop_price),
                "target": str(sig.target_price),
            },
        )
        record(
            "gate_decision",
            actor="risk_gate",
            payload={
                "signal_id": str(signal_id),
                "symbol": sig.symbol,
                "approved": decision.approved,
                "reason": decision.reason,
                "detail": decision.detail,
                "shares": decision.shares,
            },
        )

        if not decision.approved:
            log.info("signal_rejected", symbol=sig.symbol, reason=decision.reason)
            return

        intent = build_bracket(sig, decision.shares, signal_id)
        try:
            submitted = broker.submit_bracket(intent)
            log.info(
                "order_submitted",
                symbol=sig.symbol,
                shares=decision.shares,
                client_order_id=intent.client_order_id,
                broker_order_id=submitted.broker_order_id,
            )
        except Exception:
            log.exception("order_submission_failed", symbol=sig.symbol)

    feed = AlpacaBarFeed(
        api_key=settings.alpaca_api_key,
        api_secret=settings.alpaca_api_secret,
        symbols=WATCHLIST,
        store=store,
        feed=settings.alpaca_data_feed,
    )
    feed.on_bar(on_bar)

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

    async def order_poll_loop() -> None:
        while not stop_event.is_set():
            try:
                changed = sync_orders(broker, session_start_iso)
                if changed:
                    log.info("orders_synced", changed=changed)
            except Exception:
                log.exception("order_sync_failed")
            try:
                await asyncio.wait_for(stop_event.wait(), ORDER_POLL_SECONDS)
            except asyncio.TimeoutError:
                pass

    async def reconcile_loop() -> None:
        while not stop_event.is_set():
            try:
                result = reconcile_positions(broker)
                if int(result.get("drift_count", 0) or 0) > 0:
                    log.warning("reconciliation_drift", **result)
            except Exception:
                log.exception("reconciliation_failed")
            try:
                await asyncio.wait_for(stop_event.wait(), RECONCILE_SECONDS)
            except asyncio.TimeoutError:
                pass

    async def eod_flatten_loop() -> None:
        # Wait until 5 minutes before close, then flatten once, then idle until shutdown.
        flattened = False
        while not stop_event.is_set():
            secs = seconds_until_flatten()
            if secs is None:
                if not flattened and is_market_open():
                    # We may have been started past the flatten point; flatten anyway.
                    try:
                        flatten_now(broker)
                        flattened = True
                    except Exception:
                        log.exception("eod_flatten_failed")
                await _sleep_with_stop(stop_event, 60)
                continue
            try:
                await asyncio.wait_for(stop_event.wait(), min(secs, 60))
            except asyncio.TimeoutError:
                continue
            else:
                return

    feed_task = asyncio.create_task(feed.run())
    hb_task = asyncio.create_task(heartbeat_loop())
    order_task = asyncio.create_task(order_poll_loop())
    recon_task = asyncio.create_task(reconcile_loop())
    eod_task = asyncio.create_task(eod_flatten_loop())

    await stop_event.wait()
    for task in (feed_task, hb_task, order_task, recon_task, eod_task):
        task.cancel()
    log.info("paper_run_complete")
    record("runner_stop", actor="paper_run", payload={})


async def _sleep_with_stop(stop_event: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop_event.wait(), seconds)
    except asyncio.TimeoutError:
        pass


async def _fetch_starting_equity(broker: AlpacaBroker, settings) -> Decimal:  # type: ignore[no-untyped-def]
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
    asyncio.run(main())
