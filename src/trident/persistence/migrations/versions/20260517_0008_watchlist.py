"""watchlists table

Revision ID: 0008_watchlist
Revises: 0007_suggestion_tables
Create Date: 2026-05-17

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PgUUID

revision = "0008_watchlist"
down_revision = "0007_suggestion_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "watchlists",
        sa.Column("id", PgUUID(as_uuid=True), primary_key=True),
        sa.Column("symbols", sa.JSON(), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_watchlists_is_active", "watchlists", ["is_active"])


def downgrade() -> None:
    op.drop_index("ix_watchlists_is_active", table_name="watchlists")
    op.drop_table("watchlists")
