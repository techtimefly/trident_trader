from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
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
    # A bracket child leg (take-profit / stop-loss) points at its parent entry
    # order. NULL for a parent or a standalone single-leg order.
    parent_order_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("orders.id"), nullable=True, index=True
    )

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


class ManagedPosition(Base):
    """A position the runner actively manages.

    Distinct from the ``positions`` table, which the reconciler keeps as a plain
    mirror of the broker. ``ManagedPosition`` carries the *live, mutable* stop and
    target a trailing stop updates, the strategy that opened it, and a link back
    to the entry order. One row per open managed position; removed when the
    position closes.
    """

    __tablename__ = "managed_positions"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    symbol: Mapped[str] = mapped_column(String(16), unique=True)
    strategy: Mapped[str] = mapped_column(String(64))
    side: Mapped[str] = mapped_column(String(8))  # long | short
    qty: Mapped[int] = mapped_column()  # signed: negative = short
    avg_entry: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    # Live stop/target — a trailing-stop or manual adjustment mutates these.
    stop_price: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    target_price: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    entry_order_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("orders.id"), nullable=True
    )
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


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
    total_pnl: Mapped[Decimal] = mapped_column(Numeric(18, 6))  # net of fees
    avg_r: Mapped[Decimal] = mapped_column(Numeric(8, 4))
    # Honest-backtest fields (idealistic replay leaves cost columns NULL).
    mode: Mapped[str] = mapped_column(
        String(16), default="idealistic", server_default="idealistic"
    )
    slippage_bps: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    fee_per_share: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    gross_pnl: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    total_fees: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)


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
    pnl: Mapped[Decimal] = mapped_column(Numeric(18, 6))  # net of fees
    r_multiple: Mapped[Decimal] = mapped_column(Numeric(8, 4))
    # Honest-backtest fields (idealistic replay leaves these NULL).
    gross_pnl: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    entry_fee: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    exit_fee: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)


class LiveTrade(Base):
    """One closed live round-trip — an entry matched to its exit, with realized
    P&L. The live counterpart of :class:`ReplayTrade` (which is simulation only).

    ``wash_sale`` flags a realized loss followed by a re-entry in the same
    symbol within 30 days — an informational tax marker, never fed into any
    trading decision.
    """

    __tablename__ = "live_trades"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    side: Mapped[str] = mapped_column(String(8))  # long | short
    strategy: Mapped[str] = mapped_column(String(64), index=True)
    qty: Mapped[int] = mapped_column()
    entry_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    entry_price: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    exit_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    exit_price: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    gross_pnl: Mapped[Decimal] = mapped_column(Numeric(18, 6))  # before fees
    fees: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    net_pnl: Mapped[Decimal] = mapped_column(Numeric(18, 6))  # gross minus fees
    r_multiple: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    holding_period_seconds: Mapped[int] = mapped_column(BigInteger)
    wash_sale: Mapped[bool] = mapped_column(default=False)
    entry_order_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("orders.id"), nullable=True
    )
    exit_order_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("orders.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


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


class ScreenRun(Base):
    """One row per `scripts/screen.py` invocation — the criteria and counts.

    The screener is outer-ring: a failed run loses no capital, it just leaves
    the dashboard panel showing the previous run. The filter bounds are stored
    as columns (NULL = no bound) so the dashboard can echo the screen back.
    """

    __tablename__ = "screen_runs"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    universe_size: Mapped[int] = mapped_column()  # symbols requested from Alpaca
    scanned: Mapped[int] = mapped_column()  # symbols with usable bar data
    matched: Mapped[int] = mapped_column()  # symbols passing every filter
    lookback_days: Mapped[int] = mapped_column()
    # Filter bounds — NULL means that bound was not applied.
    min_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    max_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    min_avg_volume: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    min_change_pct: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    max_change_pct: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)

    results: Mapped[list[ScreenResultRow]] = relationship(back_populates="run")


class ScreenResultRow(Base):
    """One matched symbol from a screen run, with its market facts at scan time."""

    __tablename__ = "screen_results"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("screen_runs.id"), index=True
    )
    rank: Mapped[int] = mapped_column()  # 1-based position in the ranked table
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    price: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    avg_volume: Mapped[int] = mapped_column(BigInteger)
    change_pct: Mapped[Decimal] = mapped_column(Numeric(12, 4))

    run: Mapped[ScreenRun] = relationship(back_populates="results")


class SuggestionRun(Base):
    """One AI stock-suggestion run — the advisory pre-market precheck.

    Claude reviews the latest screen and suggests stocks to watch. This is
    outer-ring and advisory only: a row here is something the user reads, never
    a trading instruction. ``ok`` is False for a degraded run (no API key,
    nothing to review, an API error) — ``notice`` then carries the explanation
    and there are no child ``suggestions`` rows.
    """

    __tablename__ = "suggestion_runs"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ok: Mapped[bool] = mapped_column()  # False = degraded run (see notice)
    model: Mapped[str] = mapped_column(String(64))  # Anthropic model id, or ""
    notice: Mapped[str] = mapped_column(String(512))  # explanation when not ok
    screen_run_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("screen_runs.id"), nullable=True
    )

    suggestions: Mapped[list[SuggestionRow]] = relationship(back_populates="run")


class SuggestionRow(Base):
    """One stock the AI suggested watching, with its rationale.

    ``confidence`` is the model's own low/medium/high label — a hint for the
    reader, never a number fed into any calculation.
    """

    __tablename__ = "suggestion_rows"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("suggestion_runs.id"), index=True
    )
    rank: Mapped[int] = mapped_column()  # 1-based position, best-first
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    rationale: Mapped[str] = mapped_column(String(2048))
    confidence: Mapped[str] = mapped_column(String(16))  # low | medium | high

    run: Mapped[SuggestionRun] = relationship(back_populates="suggestions")


class Watchlist(Base):
    """DB-backed watchlist row.

    ``symbols`` is a JSON array of ticker strings. ``source`` records how this
    row was created: ``"static"`` (seeded from the module constant),
    ``"manual"`` (dashboard edit), or ``"screener"`` (promoted from a screen
    run). Only rows where ``is_active`` is True are read by
    ``resolve_watchlist()``.
    """

    __tablename__ = "watchlists"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    symbols: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
