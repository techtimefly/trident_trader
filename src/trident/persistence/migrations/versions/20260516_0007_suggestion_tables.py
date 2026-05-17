"""AI stock-suggestion run + suggestion tables

Revision ID: 0007_suggestion_tables
Revises: 0006_screen_tables
Create Date: 2026-05-16

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PgUUID

revision = "0007_suggestion_tables"
down_revision = "0006_screen_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "suggestion_runs",
        sa.Column("id", PgUUID(as_uuid=True), primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("notice", sa.String(512), nullable=False),
        sa.Column(
            "screen_run_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey("screen_runs.id"),
            nullable=True,
        ),
    )
    op.create_index("ix_suggestion_runs_started_at", "suggestion_runs", ["started_at"])

    op.create_table(
        "suggestion_rows",
        sa.Column("id", PgUUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey("suggestion_runs.id"),
            nullable=False,
        ),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("rationale", sa.String(2048), nullable=False),
        sa.Column("confidence", sa.String(16), nullable=False),
    )
    op.create_index("ix_suggestion_rows_run_id", "suggestion_rows", ["run_id"])
    op.create_index("ix_suggestion_rows_symbol", "suggestion_rows", ["symbol"])


def downgrade() -> None:
    op.drop_index("ix_suggestion_rows_symbol", table_name="suggestion_rows")
    op.drop_index("ix_suggestion_rows_run_id", table_name="suggestion_rows")
    op.drop_table("suggestion_rows")
    op.drop_index("ix_suggestion_runs_started_at", table_name="suggestion_runs")
    op.drop_table("suggestion_runs")
