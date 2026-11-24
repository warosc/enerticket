"""Marcas de seeds aplicados una sola vez (``seed_state``).

Revision ID: 0005_seed_state
Revises: 0004_attachments
Create Date: 2026-11-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_seed_state"
down_revision: str | None = "0004_attachments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "seed_state",
        sa.Column("key", sa.String(100), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("data", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("key", name="pk_seed_state"),
    )


def downgrade() -> None:
    op.drop_table("seed_state")
