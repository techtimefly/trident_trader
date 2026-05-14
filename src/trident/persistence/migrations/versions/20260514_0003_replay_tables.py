"""replay tables

Revision ID: 0003_replay_tables
Revises: 0002_system_state
Create Date: 2026-05-14

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003_replay_tables"
down_revision = "0002_system_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "replay_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("first_day", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_day", sa.DateTime(timezone=True), nullable=False),
        sa.Column("days", sa.Integer(), nullable=False),
        sa.Column("equity", sa.Numeric(18, 6), nullable=False),
        sa.Column("watchlist", sa.JSON(), nullable=False),
        sa.Column("strategy", sa.String(length=64), nullable=False),
        sa.Column("num_trades", sa.Integer(), nullable=False),
        sa.Column("wins", sa.Integer(), nullable=False),
        sa.Column("losses", sa.Integer(), nullable=False),
        sa.Column("total_pnl", sa.Numeric(18, 6), nullable=False),
        sa.Column("avg_r", sa.Numeric(8, 4), nullable=False),
    )
    op.create_index("ix_replay_runs_started_at", "replay_runs", ["started_at"])

    op.create_table(
        "replay_trades",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("replay_runs.id"),
            nullable=False,
        ),
        sa.Column("trade_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("side", sa.String(length=8), nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("entry_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("entry_price", sa.Numeric(18, 6), nullable=False),
        sa.Column("stop_price", sa.Numeric(18, 6), nullable=False),
        sa.Column("target_price", sa.Numeric(18, 6), nullable=False),
        sa.Column("exit_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("exit_reason", sa.String(length=16), nullable=False),
        sa.Column("exit_price", sa.Numeric(18, 6), nullable=False),
        sa.Column("pnl", sa.Numeric(18, 6), nullable=False),
        sa.Column("r_multiple", sa.Numeric(8, 4), nullable=False),
    )
    op.create_index("ix_replay_trades_run_id", "replay_trades", ["run_id"])
    op.create_index("ix_replay_trades_trade_date", "replay_trades", ["trade_date"])
    op.create_index("ix_replay_trades_symbol", "replay_trades", ["symbol"])


def downgrade() -> None:
    op.drop_index("ix_replay_trades_symbol", table_name="replay_trades")
    op.drop_index("ix_replay_trades_trade_date", table_name="replay_trades")
    op.drop_index("ix_replay_trades_run_id", table_name="replay_trades")
    op.drop_table("replay_trades")
    op.drop_index("ix_replay_runs_started_at", table_name="replay_runs")
    op.drop_table("replay_runs")
