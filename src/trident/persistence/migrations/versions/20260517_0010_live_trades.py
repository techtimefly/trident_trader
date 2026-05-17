"""live_trades round-trip table

Revision ID: 0010_live_trades
Revises: 0009_managed_positions
Create Date: 2026-05-17

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PgUUID

revision = "0010_live_trades"
down_revision = "0009_managed_positions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "live_trades",
        sa.Column("id", PgUUID(as_uuid=True), primary_key=True),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("strategy", sa.String(64), nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("entry_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("entry_price", sa.Numeric(18, 6), nullable=False),
        sa.Column("exit_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("exit_price", sa.Numeric(18, 6), nullable=False),
        sa.Column("gross_pnl", sa.Numeric(18, 6), nullable=False),
        sa.Column("fees", sa.Numeric(18, 6), nullable=False),
        sa.Column("net_pnl", sa.Numeric(18, 6), nullable=False),
        sa.Column("r_multiple", sa.Numeric(8, 4), nullable=True),
        sa.Column("holding_period_seconds", sa.BigInteger(), nullable=False),
        sa.Column("wash_sale", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("entry_order_id", PgUUID(as_uuid=True), sa.ForeignKey("orders.id"), nullable=True),
        sa.Column("exit_order_id", PgUUID(as_uuid=True), sa.ForeignKey("orders.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_live_trades_symbol", "live_trades", ["symbol"])
    op.create_index("ix_live_trades_strategy", "live_trades", ["strategy"])
    op.create_index("ix_live_trades_entry_ts", "live_trades", ["entry_ts"])


def downgrade() -> None:
    op.drop_index("ix_live_trades_entry_ts", table_name="live_trades")
    op.drop_index("ix_live_trades_strategy", table_name="live_trades")
    op.drop_index("ix_live_trades_symbol", table_name="live_trades")
    op.drop_table("live_trades")
