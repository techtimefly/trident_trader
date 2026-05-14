"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-14

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("actor", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
    )
    op.create_index("ix_audit_events_ts", "audit_events", ["ts"])
    op.create_index("ix_audit_events_event_type", "audit_events", ["event_type"])

    # Enforce append-only with a trigger. Catches programmer error;
    # not a security boundary (a superuser can DROP the trigger).
    op.execute(
        """
        CREATE OR REPLACE FUNCTION audit_events_block_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'audit_events is append-only (operation: %)', TG_OP;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_events_no_update_delete
        BEFORE UPDATE OR DELETE ON audit_events
        FOR EACH ROW EXECUTE FUNCTION audit_events_block_mutation();
        """
    )

    op.create_table(
        "signals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("strategy", sa.String(length=64), nullable=False),
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("side", sa.String(length=8), nullable=False),
        sa.Column("entry_price", sa.Numeric(18, 6), nullable=False),
        sa.Column("stop_price", sa.Numeric(18, 6), nullable=False),
        sa.Column("target_price", sa.Numeric(18, 6), nullable=False),
        sa.Column("meta", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("gate_decision", sa.String(length=16), nullable=True),
        sa.Column("gate_reason", sa.String(length=256), nullable=True),
    )
    op.create_index("ix_signals_ts", "signals", ["ts"])
    op.create_index("ix_signals_strategy", "signals", ["strategy"])
    op.create_index("ix_signals_symbol", "signals", ["symbol"])

    op.create_table(
        "orders",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("signal_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("signals.id"), nullable=True),
        sa.Column("client_order_id", sa.String(length=64), unique=True, nullable=False),
        sa.Column("broker_order_id", sa.String(length=64), nullable=True),
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("side", sa.String(length=8), nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("order_type", sa.String(length=16), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("filled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("avg_fill_price", sa.Numeric(18, 6), nullable=True),
        sa.Column("raw", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.create_index("ix_orders_state", "orders", ["state"])
    op.create_index("ix_orders_symbol", "orders", ["symbol"])
    op.create_index("ix_orders_broker_order_id", "orders", ["broker_order_id"])

    op.create_table(
        "fills",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("price", sa.Numeric(18, 6), nullable=False),
    )

    op.create_table(
        "positions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("symbol", sa.String(length=16), unique=True, nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("avg_entry", sa.Numeric(18, 6), nullable=False),
        sa.Column("stop_price", sa.Numeric(18, 6), nullable=False),
        sa.Column("target_price", sa.Numeric(18, 6), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "bars",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("timeframe", sa.String(length=8), nullable=False),
        sa.Column("open", sa.Numeric(18, 6), nullable=False),
        sa.Column("high", sa.Numeric(18, 6), nullable=False),
        sa.Column("low", sa.Numeric(18, 6), nullable=False),
        sa.Column("close", sa.Numeric(18, 6), nullable=False),
        sa.Column("volume", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("symbol", "ts", "timeframe", name="uq_bars_symbol_ts_tf"),
    )
    op.create_index("ix_bars_symbol_tf_ts", "bars", ["symbol", "timeframe", "ts"])

    op.create_table(
        "daily_account_snapshots",
        sa.Column("trading_day", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("starting_equity", sa.Numeric(18, 6), nullable=False),
        sa.Column("current_equity", sa.Numeric(18, 6), nullable=False),
        sa.Column("realized_pnl", sa.Numeric(18, 6), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("daily_account_snapshots")
    op.drop_index("ix_bars_symbol_tf_ts", table_name="bars")
    op.drop_table("bars")
    op.drop_table("positions")
    op.drop_table("fills")
    op.drop_index("ix_orders_broker_order_id", table_name="orders")
    op.drop_index("ix_orders_symbol", table_name="orders")
    op.drop_index("ix_orders_state", table_name="orders")
    op.drop_table("orders")
    op.drop_index("ix_signals_symbol", table_name="signals")
    op.drop_index("ix_signals_strategy", table_name="signals")
    op.drop_index("ix_signals_ts", table_name="signals")
    op.drop_table("signals")
    op.execute("DROP TRIGGER IF EXISTS audit_events_no_update_delete ON audit_events;")
    op.execute("DROP FUNCTION IF EXISTS audit_events_block_mutation();")
    op.drop_index("ix_audit_events_event_type", table_name="audit_events")
    op.drop_index("ix_audit_events_ts", table_name="audit_events")
    op.drop_table("audit_events")
