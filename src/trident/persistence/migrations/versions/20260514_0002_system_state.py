"""system_state table

Revision ID: 0002_system_state
Revises: 0001_initial
Create Date: 2026-05-14

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_system_state"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "system_state",
        sa.Column("key", sa.String(length=64), primary_key=True),
        sa.Column("value", sa.String(length=1024), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("system_state")
