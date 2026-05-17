"""backtest cost columns

Revision ID: 0005_backtest_costs
Revises: 0004_daily_plans
Create Date: 2026-05-16

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005_backtest_costs"
down_revision = "0004_daily_plans"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "replay_runs",
        sa.Column("mode", sa.String(16), nullable=False, server_default="idealistic"),
    )
    op.add_column("replay_runs", sa.Column("slippage_bps", sa.Numeric(8, 4), nullable=True))
    op.add_column("replay_runs", sa.Column("fee_per_share", sa.Numeric(12, 6), nullable=True))
    op.add_column("replay_runs", sa.Column("gross_pnl", sa.Numeric(18, 6), nullable=True))
    op.add_column("replay_runs", sa.Column("total_fees", sa.Numeric(18, 6), nullable=True))
    op.add_column("replay_trades", sa.Column("gross_pnl", sa.Numeric(18, 6), nullable=True))
    op.add_column("replay_trades", sa.Column("entry_fee", sa.Numeric(18, 6), nullable=True))
    op.add_column("replay_trades", sa.Column("exit_fee", sa.Numeric(18, 6), nullable=True))


def downgrade() -> None:
    op.drop_column("replay_trades", "exit_fee")
    op.drop_column("replay_trades", "entry_fee")
    op.drop_column("replay_trades", "gross_pnl")
    op.drop_column("replay_runs", "total_fees")
    op.drop_column("replay_runs", "gross_pnl")
    op.drop_column("replay_runs", "fee_per_share")
    op.drop_column("replay_runs", "slippage_bps")
    op.drop_column("replay_runs", "mode")
