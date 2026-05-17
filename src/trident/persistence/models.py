from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class AuditEvent(Base):
    """Append-only event log. The trigger in the initial migration blocks UPDATE/DELETE."""

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    actor: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class Signal(Base):
    __tablename__ = "signals"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    strategy: Mapped[str] = mapped_column(String(64), index=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    side: Mapped[str] = mapped_column(String(8))
    entry_price: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    stop_price: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    target_price: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    gate_decision: Mapped[str | None] = mapped_column(String(16), nullable=True)
    gate_reason: Mapped[str | None] = mapped_column(String(256), nullable=True)

    orders: Mapped[list[Order]] = relationship(back_populates="signal")


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    signal_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("signals.id"), nullable=True
    )
    client_order_id: Mapped[str] = mapped_column(String(64), unique=True)
    broker_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    symbol: Mapped[str] = mapped_column(String(16), index=True)
    side: Mapped[str] = mapped_column(String(8))
    qty: Mapped[int] = mapped_column()
    order_type: Mapped[str] = mapped_column(String(16))
    state: Mapped[str] = mapped_column(String(16), index=True)

    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    filled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    avg_fill_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    raw: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    signal: Mapped[Signal | None] = relationship(back_populates="orders")
    fills: Mapped[list[Fill]] = relationship(back_populates="order")


class Fill(Base):
    __tablename__ = "fills"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), ForeignKey("orders.id"))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    qty: Mapped[int] = mapped_column()
    price: Mapped[Decimal] = mapped_column(Numeric(18, 6))

    order: Mapped[Order] = relationship(back_populates="fills")


class Position(Base):
    __tablename__ = "positions"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    symbol: Mapped[str] = mapped_column(String(16), unique=True)
    qty: Mapped[int] = mapped_column()
    avg_entry: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    stop_price: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    target_price: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Bar(Base):
    __tablename__ = "bars"
    __table_args__ = (
        UniqueConstraint("symbol", "ts", "timeframe", name="uq_bars_symbol_ts_tf"),
        Index("ix_bars_symbol_tf_ts", "symbol", "timeframe", "ts"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    timeframe: Mapped[str] = mapped_column(String(8))
    open: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    high: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    low: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    close: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    volume: Mapped[int] = mapped_column(BigInteger)


class DailyAccountSnapshot(Base):
    """One row per trading day. Used to compute daily P&L and enforce the loss limit."""

    __tablename__ = "daily_account_snapshots"

    trading_day: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    starting_equity: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    current_equity: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    realized_pnl: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=Decimal("0"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SystemState(Base):
    """Tiny key/value table. Holds the kill switch flag and the shadow-run heartbeat."""

    __tablename__ = "system_state"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(1024))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ReplayRun(Base):
    """One row per `scripts/replay.py` invocation."""

    __tablename__ = "replay_runs"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    first_day: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_day: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    days: Mapped[int] = mapped_column()
    equity: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    watchlist: Mapped[dict[str, Any]] = mapped_column(JSON)
    strategy: Mapped[str] = mapped_column(String(64))
    num_trades: Mapped[int] = mapped_column()
    wins: Mapped[int] = mapped_column()
    losses: Mapped[int] = mapped_column()
    total_pnl: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    avg_r: Mapped[Decimal] = mapped_column(Numeric(8, 4))


class ReplayTrade(Base):
    __tablename__ = "replay_trades"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("replay_runs.id"), index=True
    )
    trade_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    side: Mapped[str] = mapped_column(String(8))
    qty: Mapped[int] = mapped_column()
    entry_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    entry_price: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    stop_price: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    target_price: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    exit_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    exit_reason: Mapped[str] = mapped_column(String(16))
    exit_price: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    pnl: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    r_multiple: Mapped[Decimal] = mapped_column(Numeric(8, 4))


class DailyPlan(Base):
    """One row per trading day — the user's per-day guardrails (capital budget,
    day-trade cap). A missing row, or a NULL column, means that cap is not set.
    """

    __tablename__ = "daily_plans"

    trading_day: Mapped[date] = mapped_column(Date, primary_key=True)
    budget_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    max_day_trades: Mapped[int | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
