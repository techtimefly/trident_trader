"""managed_positions table + order leg linkage

Revision ID: 0009_managed_positions
Revises: 0008_watchlist
Create Date: 2026-05-17

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PgUUID

revision = "0009_managed_positions"
down_revision = "0008_watchlist"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Order-leg linkage: a bracket child (TP/SL) points at its parent entry.
    op.add_column(
        "orders",
        sa.Column("parent_order_id", PgUUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_orders_parent_order_id", "orders", ["parent_order_id"])
    op.create_foreign_key(
        "fk_orders_parent_order_id", "orders", "orders", ["parent_order_id"], ["id"]
    )

    op.create_table(
        "managed_positions",
        sa.Column("id", PgUUID(as_uuid=True), primary_key=True),
        sa.Column("symbol", sa.String(16), nullable=False, unique=True),
        sa.Column("strategy", sa.String(64), nullable=False),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("avg_entry", sa.Numeric(18, 6), nullable=False),
        sa.Column("stop_price", sa.Numeric(18, 6), nullable=False),
        sa.Column("target_price", sa.Numeric(18, 6), nullable=False),
        sa.Column(
            "entry_order_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey("orders.id"),
            nullable=True,
        ),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("managed_positions")
    op.drop_constraint("fk_orders_parent_order_id", "orders", type_="foreignkey")
    op.drop_index("ix_orders_parent_order_id", table_name="orders")
    op.drop_column("orders", "parent_order_id")
