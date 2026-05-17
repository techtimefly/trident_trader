"""screener run + result tables

Revision ID: 0006_screen_tables
Revises: 0005_backtest_costs
Create Date: 2026-05-16

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PgUUID

revision = "0006_screen_tables"
down_revision = "0005_backtest_costs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "screen_runs",
        sa.Column("id", PgUUID(as_uuid=True), primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("universe_size", sa.Integer(), nullable=False),
        sa.Column("scanned", sa.Integer(), nullable=False),
        sa.Column("matched", sa.Integer(), nullable=False),
        sa.Column("lookback_days", sa.Integer(), nullable=False),
        sa.Column("min_price", sa.Numeric(18, 6), nullable=True),
        sa.Column("max_price", sa.Numeric(18, 6), nullable=True),
        sa.Column("min_avg_volume", sa.BigInteger(), nullable=True),
        sa.Column("min_change_pct", sa.Numeric(12, 4), nullable=True),
        sa.Column("max_change_pct", sa.Numeric(12, 4), nullable=True),
    )
    op.create_index("ix_screen_runs_started_at", "screen_runs", ["started_at"])

    op.create_table(
        "screen_results",
        sa.Column("id", PgUUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey("screen_runs.id"),
            nullable=False,
        ),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("price", sa.Numeric(18, 6), nullable=False),
        sa.Column("avg_volume", sa.BigInteger(), nullable=False),
        sa.Column("change_pct", sa.Numeric(12, 4), nullable=False),
    )
    op.create_index("ix_screen_results_run_id", "screen_results", ["run_id"])
    op.create_index("ix_screen_results_symbol", "screen_results", ["symbol"])


def downgrade() -> None:
    op.drop_index("ix_screen_results_symbol", table_name="screen_results")
    op.drop_index("ix_screen_results_run_id", table_name="screen_results")
    op.drop_table("screen_results")
    op.drop_index("ix_screen_runs_started_at", table_name="screen_runs")
    op.drop_table("screen_runs")
