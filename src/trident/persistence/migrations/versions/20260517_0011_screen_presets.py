"""screen presets + FMP filter columns

Revision ID: 0011_screen_presets
Revises: 0010_live_trades
Create Date: 2026-05-17

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PgUUID

revision = "0011_screen_presets"
down_revision = "0010_live_trades"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "screen_presets",
        sa.Column("id", PgUUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("criteria", sa.JSON(), nullable=False),
        sa.Column("lookback_days", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("name", name="uq_screen_presets_name"),
    )
    op.create_index("ix_screen_presets_is_active", "screen_presets", ["is_active"])

    # FMP-sourced filter bounds on each screen run (additive, all nullable so
    # existing rows stay valid). NULL / NULL-JSON means the bound was not set.
    op.add_column("screen_runs", sa.Column("min_market_cap", sa.BigInteger(), nullable=True))
    op.add_column("screen_runs", sa.Column("max_market_cap", sa.BigInteger(), nullable=True))
    op.add_column("screen_runs", sa.Column("sectors", sa.JSON(), nullable=True))
    op.add_column("screen_runs", sa.Column("exchanges", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("screen_runs", "exchanges")
    op.drop_column("screen_runs", "sectors")
    op.drop_column("screen_runs", "max_market_cap")
    op.drop_column("screen_runs", "min_market_cap")
    op.drop_index("ix_screen_presets_is_active", table_name="screen_presets")
    op.drop_table("screen_presets")
