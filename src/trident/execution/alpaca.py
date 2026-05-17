"""Alpaca trading adapter — paper account only.

Every method:
  - Is constructed against `paper-api.alpaca.markets` (asserted at startup).
  - Returns normalized snapshots rather than alpaca-py objects so callers don't
    couple to the SDK shape.
  - Audits every action (submit, cancel, close) before returning.

Failures bubble up as exceptions — callers (runner, reconciliation loop) decide
what to do. The dead-man's switch handles the case where the whole process dies.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from trident.audit.log import get_logger, record
from trident.execution.broker import OrderSnapshot, PositionSnapshot, SubmittedOrder
from trident.execution.orders import BracketOrderIntent, OrderIntent
from trident.settings import get_settings

log = get_logger("execution.alpaca")


class AlpacaBroker:
    def __init__(self) -> None:
        settings = get_settings()
        if not settings.is_paper:
            raise RuntimeError(
                f"AlpacaBroker refuses non-paper base URL: {settings.alpaca_base_url!r}. "
                "Live trading is intentionally not wired in v0.2."
            )
        from alpaca.trading.client import TradingClient

        self._settings = settings
        self._client = TradingClient(
            api_key=settings.alpaca_api_key,
            secret_key=settings.alpaca_api_secret,
            paper=True,
        )

    def submit_bracket(self, intent: BracketOrderIntent) -> SubmittedOrder:
        from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
        from alpaca.trading.requests import (
            LimitOrderRequest,
            StopLossRequest,
            TakeProfitRequest,
        )

        side = OrderSide.BUY if intent.side == "buy" else OrderSide.SELL
        req = LimitOrderRequest(
            symbol=intent.symbol,
            qty=intent.qty,
            side=side,
            time_in_force=TimeInForce.DAY,
            limit_price=float(intent.limit_price),
            order_class=OrderClass.BRACKET,
            take_profit=TakeProfitRequest(limit_price=float(intent.take_profit)),
            stop_loss=StopLossRequest(stop_price=float(intent.stop_loss)),
            client_order_id=intent.client_order_id,
        )

        record(
            "order_submitting",
            actor="execution.alpaca",
            payload=intent.to_audit_payload(),
        )
        try:
            order = self._client.submit_order(req)
        except Exception as exc:
            # If idempotency key collided (we already submitted this signal), Alpaca
            # returns 422. Surface it but don't crash the runner.
            record(
                "order_submit_failed",
                actor="execution.alpaca",
                payload={
                    "client_order_id": intent.client_order_id,
                    "error": str(exc)[:500],
                },
            )
            raise

        submitted = SubmittedOrder(
            broker_order_id=str(order.id),
            client_order_id=intent.client_order_id,
            status=str(order.status).split(".")[-1].lower(),
        )
        record(
            "order_submitted",
            actor="execution.alpaca",
            payload={
                "client_order_id": submitted.client_order_id,
                "broker_order_id": submitted.broker_order_id,
                "status": submitted.status,
            },
        )
        return submitted

    def cancel_all_orders(self) -> int:
        cancelled = self._client.cancel_orders()
        count = len(cancelled) if cancelled else 0
        record("orders_cancelled_all", actor="execution.alpaca", payload={"count": count})
        return count

    def close_all_positions(self, cancel_orders: bool = True) -> int:
        responses = self._client.close_all_positions(cancel_orders=cancel_orders)
        count = len(responses) if responses else 0
        record(
            "positions_closed_all",
            actor="execution.alpaca",
            payload={"count": count, "cancel_orders": cancel_orders},
        )
        return count

    def list_open_orders(self) -> list[OrderSnapshot]:
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        req = GetOrdersRequest(status=QueryOrderStatus.OPEN, limit=500)
        return [_snapshot(o) for o in self._client.get_orders(filter=req)]

    def list_orders_since(self, iso_ts: str) -> list[OrderSnapshot]:
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        req = GetOrdersRequest(status=QueryOrderStatus.ALL, after=iso_ts, limit=500)
        return [_snapshot(o) for o in self._client.get_orders(filter=req)]

    def get_order_by_client_id(self, client_order_id: str) -> OrderSnapshot | None:
        try:
            o = self._client.get_order_by_client_id(client_order_id)
        except Exception:
            return None
        return _snapshot(o) if o is not None else None

    def list_positions(self) -> list[PositionSnapshot]:
        rows: list[PositionSnapshot] = []
        for p in self._client.get_all_positions():
            rows.append(
                PositionSnapshot(
                    symbol=str(p.symbol),
                    qty=int(float(p.qty)),
                    avg_entry_price=Decimal(str(p.avg_entry_price)),
                    market_value=Decimal(str(p.market_value)),
                    unrealized_pl=Decimal(str(p.unrealized_pl)),
                )
            )
        return rows

    # --- Active position management ---------------------------------------

    def submit_order(self, intent: OrderIntent) -> SubmittedOrder:
        """Submit a single-leg market or limit order (a scale-in add or an exit)."""
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest

        side = OrderSide.BUY if intent.side == "buy" else OrderSide.SELL
        if intent.order_type == "limit":
            if intent.limit_price is None:
                raise ValueError("limit order requires a limit_price")
            req: Any = LimitOrderRequest(
                symbol=intent.symbol,
                qty=intent.qty,
                side=side,
                time_in_force=TimeInForce.DAY,
                limit_price=float(intent.limit_price),
                client_order_id=intent.client_order_id,
            )
        else:
            req = MarketOrderRequest(
                symbol=intent.symbol,
                qty=intent.qty,
                side=side,
                time_in_force=TimeInForce.DAY,
                client_order_id=intent.client_order_id,
            )

        record("order_submitting", actor="execution.alpaca", payload=intent.to_audit_payload())
        try:
            # The SDK types this Order | RawData; we never pass raw_data, so it
            # is always an Order. Annotate Any to keep attribute access clean.
            order: Any = self._client.submit_order(req)
        except Exception as exc:
            record(
                "order_submit_failed",
                actor="execution.alpaca",
                payload={"client_order_id": intent.client_order_id, "error": str(exc)[:500]},
            )
            raise

        submitted = SubmittedOrder(
            broker_order_id=str(order.id),
            client_order_id=intent.client_order_id,
            status=str(order.status).split(".")[-1].lower(),
        )
        record(
            "order_submitted",
            actor="execution.alpaca",
            payload={
                "client_order_id": submitted.client_order_id,
                "broker_order_id": submitted.broker_order_id,
                "status": submitted.status,
                "reason": intent.reason,
            },
        )
        return submitted

    def cancel_order(self, broker_order_id: str) -> None:
        """Cancel one order by its broker id."""
        try:
            self._client.cancel_order_by_id(broker_order_id)
        except Exception as exc:
            record(
                "order_cancel_failed",
                actor="execution.alpaca",
                payload={"broker_order_id": broker_order_id, "error": str(exc)[:500]},
            )
            raise
        record(
            "order_cancelled",
            actor="execution.alpaca",
            payload={"broker_order_id": broker_order_id},
        )

    def replace_order(
        self,
        broker_order_id: str,
        *,
        qty: int | None = None,
        limit_price: Decimal | None = None,
        stop_price: Decimal | None = None,
    ) -> SubmittedOrder:
        """Modify a live order in place — how a trailing stop moves its stop leg.

        Alpaca's replace returns a NEW order id; the returned SubmittedOrder
        carries it so callers can re-track the order.
        """
        from alpaca.trading.requests import ReplaceOrderRequest

        req = ReplaceOrderRequest(
            qty=qty,
            limit_price=float(limit_price) if limit_price is not None else None,
            stop_price=float(stop_price) if stop_price is not None else None,
        )
        record(
            "order_replacing",
            actor="execution.alpaca",
            payload={
                "broker_order_id": broker_order_id,
                "qty": qty,
                "limit_price": str(limit_price) if limit_price is not None else None,
                "stop_price": str(stop_price) if stop_price is not None else None,
            },
        )
        try:
            order: Any = self._client.replace_order_by_id(broker_order_id, req)
        except Exception as exc:
            record(
                "order_replace_failed",
                actor="execution.alpaca",
                payload={"broker_order_id": broker_order_id, "error": str(exc)[:500]},
            )
            raise

        submitted = SubmittedOrder(
            broker_order_id=str(order.id),
            client_order_id=str(order.client_order_id) if order.client_order_id else "",
            status=str(order.status).split(".")[-1].lower(),
        )
        record(
            "order_replaced",
            actor="execution.alpaca",
            payload={
                "old_broker_order_id": broker_order_id,
                "new_broker_order_id": submitted.broker_order_id,
                "status": submitted.status,
            },
        )
        return submitted

    def close_position(self, symbol: str, qty: int | None = None) -> SubmittedOrder:
        """Close one position. ``qty`` None closes it entirely; a value does a
        partial close (the scale-out primitive)."""
        from alpaca.trading.requests import ClosePositionRequest

        close_options = ClosePositionRequest(qty=str(qty)) if qty is not None else None
        record(
            "position_closing",
            actor="execution.alpaca",
            payload={"symbol": symbol, "qty": qty},
        )
        try:
            order: Any = self._client.close_position(symbol, close_options=close_options)
        except Exception as exc:
            record(
                "position_close_failed",
                actor="execution.alpaca",
                payload={"symbol": symbol, "qty": qty, "error": str(exc)[:500]},
            )
            raise

        submitted = SubmittedOrder(
            broker_order_id=str(order.id),
            client_order_id=str(order.client_order_id) if order.client_order_id else "",
            status=str(order.status).split(".")[-1].lower(),
        )
        record(
            "position_closed_one",
            actor="execution.alpaca",
            payload={
                "symbol": symbol,
                "qty": qty,
                "broker_order_id": submitted.broker_order_id,
            },
        )
        return submitted


def _snapshot(o: Any) -> OrderSnapshot:
    return OrderSnapshot(
        broker_order_id=str(o.id),
        client_order_id=str(o.client_order_id) if o.client_order_id else "",
        symbol=str(o.symbol),
        side=str(o.side).split(".")[-1].lower(),
        qty=int(float(o.qty)) if o.qty is not None else 0,
        filled_qty=int(float(o.filled_qty)) if o.filled_qty else 0,
        avg_fill_price=(
            Decimal(str(o.filled_avg_price)) if o.filled_avg_price else None
        ),
        status=str(o.status).split(".")[-1].lower(),
        order_class=str(o.order_class).split(".")[-1].lower() if o.order_class else "",
        submitted_at=str(o.submitted_at) if o.submitted_at else "",
        filled_at=str(o.filled_at) if o.filled_at else None,
    )
