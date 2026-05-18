"""multi-watchlist: named watchlists

Revision ID: 0012_multi_watchlist
Revises: 0011_screen_presets
Create Date: 2026-05-17

Adds a unique ``name`` to each watchlist so several named watchlists can coexist
(exactly one active at a time, the one ``resolve_watchlist()`` reads).

The previous design treated the table as an append-only edit history of a single
logical list — one active row, the rest inactive snapshots. That history does not
fit the named-watchlist model, so it is collapsed here: inactive rows are dropped
and the surviving active row (at most one, by construction) is named "Default".
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012_multi_watchlist"
down_revision = "0011_screen_presets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("watchlists", sa.Column("name", sa.String(64), nullable=True))
    # Collapse the old append-only history: keep only the active row.
    op.execute("DELETE FROM watchlists WHERE is_active = false")
    # Name whatever survives — there is at most one active row by construction.
    op.execute("UPDATE watchlists SET name = 'Default' WHERE name IS NULL")
    op.alter_column("watchlists", "name", existing_type=sa.String(64), nullable=False)
    op.create_unique_constraint("uq_watchlists_name", "watchlists", ["name"])


def downgrade() -> None:
    op.drop_constraint("uq_watchlists_name", "watchlists", type_="unique")
    op.drop_column("watchlists", "name")
