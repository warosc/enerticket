"""Adjuntos de tickets (metadatos; el contenido vive en MinIO/S3).

Revision ID: 0004_attachments
Revises: 0003_flows
Create Date: 2026-11-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_attachments"
down_revision: str | None = "0003_flows"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ticket_attachments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ticket_id", sa.Integer(), nullable=False),
        sa.Column("object_key", sa.String(255), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("mime", sa.String(150), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("uploaded_by", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_ticket_attachments"),
        sa.ForeignKeyConstraint(
            ["ticket_id"],
            ["tickets.id"],
            name="fk_ticket_attachments_ticket_id_tickets",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["uploaded_by"], ["users.id"], name="fk_ticket_attachments_uploaded_by_users"
        ),
        sa.UniqueConstraint("object_key", name="uq_ticket_attachments_object_key"),
        sa.CheckConstraint("size >= 0", name=op.f("ck_ticket_attachments_size_non_negative")),
    )
    op.create_index(
        "ix_ticket_attachments_ticket_id_deleted_at",
        "ticket_attachments",
        ["ticket_id", "deleted_at"],
    )
    op.create_index("ix_ticket_attachments_uploaded_by", "ticket_attachments", ["uploaded_by"])


def downgrade() -> None:
    op.drop_index("ix_ticket_attachments_uploaded_by", table_name="ticket_attachments")
    op.drop_index("ix_ticket_attachments_ticket_id_deleted_at", table_name="ticket_attachments")
    op.drop_table("ticket_attachments")
