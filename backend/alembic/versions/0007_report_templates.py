"""Plantillas del generador de reportes (``report_templates``).

Revision ID: 0007_report_templates
Revises: 0006_catalog
Create Date: 2026-11-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_report_templates"
down_revision: str | None = "0006_catalog"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "report_templates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("owner_id", sa.Integer(), nullable=True),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("definition", sa.JSON(), nullable=False),
        sa.Column("shared_roles", sa.JSON(), nullable=False),
        sa.Column("shared_department_ids", sa.JSON(), nullable=False),
        sa.Column("schedule", sa.JSON(), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_status", sa.String(40), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_report_templates"),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["users.id"],
            name="fk_report_templates_owner_id_users", ondelete="SET NULL",
        ),
    )
    op.create_index("ix_report_templates_owner_id", "report_templates", ["owner_id"])
    op.create_index("ix_report_templates_next_run_at", "report_templates", ["next_run_at"])


def downgrade() -> None:
    op.drop_index("ix_report_templates_next_run_at", table_name="report_templates")
    op.drop_index("ix_report_templates_owner_id", table_name="report_templates")
    op.drop_table("report_templates")
