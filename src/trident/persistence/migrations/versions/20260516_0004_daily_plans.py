"""daily plans

Revision ID: 0004_daily_plans
Revises: 0003_replay_tables
Create Date: 2026-05-16

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_daily_plans"
down_revision = "0003_replay_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "daily_plans",
        sa.Column("trading_day", sa.Date(), primary_key=True),
        sa.Column("budget_pct", sa.Numeric(8, 4), nullable=True),
        sa.Column("max_day_trades", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("daily_plans")
