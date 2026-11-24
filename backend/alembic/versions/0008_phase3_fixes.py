"""Correcciones de la Fase 3: valor del AD del usuario y reintentos de reportes programados.

Revision ID: 0008_phase3_fixes
Revises: 0007_report_templates
Create Date: 2026-11-24

- ``users.ad_department_value``: último valor normalizado del atributo ``department`` del AD.
  Permite distinguir un cambio real en el AD de un ajuste manual y aplicar de inmediato la
  asignación de un alias a los usuarios que lo traen. Las filas existentes quedan nulas (se
  completan en el siguiente inicio de sesión).
- ``report_templates.retry_count``: reintentos consecutivos del envío programado tras un error.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_phase3_fixes"
down_revision: str | None = "0007_report_templates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("ad_department_value", sa.String(200), nullable=True))
        batch.create_index("ix_users_ad_department_value", ["ad_department_value"])
    with op.batch_alter_table("report_templates") as batch:
        batch.add_column(
            sa.Column("retry_count", sa.Integer(), nullable=False, server_default=sa.text("0"))
        )


def downgrade() -> None:
    with op.batch_alter_table("report_templates") as batch:
        batch.drop_column("retry_count")
    with op.batch_alter_table("users") as batch:
        batch.drop_index("ix_users_ad_department_value")
        batch.drop_column("ad_department_value")
