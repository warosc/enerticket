"""Catálogo de servicio: gerencias, alias AD, categorías, subcategorías, proveedores y
columnas nuevas en departamentos, usuarios, tipos, tickets y políticas SLA.

Revision ID: 0006_catalog
Revises: 0005_seed_state
Create Date: 2026-11-24

Los departamentos existentes quedan con ``serves_tickets = true`` (hasta ahora todos
recibían tickets). En SQLite las columnas con clave foránea se agregan en modo batch
(recreación de la tabla); en PostgreSQL son ``ALTER TABLE`` normales.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_catalog"
down_revision: str | None = "0005_seed_state"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMPTY_JSON_LIST = sa.text("'[]'")
TICKET_INDEXED = ("category_id", "subcategory_id", "provider_id", "requester_department_id", "parent_ticket_id")


def upgrade() -> None:
    # ------------------------------------------------------------------ tablas nuevas
    op.create_table(
        "gerencias",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("external_group", sa.String(255), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.PrimaryKeyConstraint("id", name="pk_gerencias"),
        sa.UniqueConstraint("name", name="uq_gerencias_name"),
    )

    with op.batch_alter_table("departments") as batch:
        batch.add_column(sa.Column("gerencia_id", sa.Integer(), nullable=True))
        batch.add_column(
            sa.Column("serves_tickets", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.add_column(sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()))
        batch.create_foreign_key(
            "fk_departments_gerencia_id_gerencias", "gerencias", ["gerencia_id"], ["id"],
            ondelete="SET NULL",
        )
        batch.create_index("ix_departments_gerencia_id", ["gerencia_id"])
    # Hasta ahora todos los departamentos atendían tickets.
    op.execute(sa.text("UPDATE departments SET serves_tickets = :value").bindparams(value=True))

    op.create_table(
        "department_aliases",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("normalized_value", sa.String(200), nullable=False),
        sa.Column("raw_value", sa.String(200), nullable=False),
        sa.Column("department_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_department_aliases"),
        sa.UniqueConstraint("normalized_value", name="uq_department_aliases_normalized_value"),
        sa.ForeignKeyConstraint(
            ["department_id"], ["departments.id"],
            name="fk_department_aliases_department_id_departments", ondelete="SET NULL",
        ),
    )
    op.create_index("ix_department_aliases_department_id", "department_aliases", ["department_id"])

    op.create_table(
        "ticket_categories",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("department_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("type_keys", sa.JSON(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_ticket_categories"),
        sa.UniqueConstraint("department_id", "name", name="uq_ticket_categories_department_name"),
        sa.ForeignKeyConstraint(
            ["department_id"], ["departments.id"], name="fk_ticket_categories_department_id_departments"
        ),
    )
    op.create_index("ix_ticket_categories_department_id", "ticket_categories", ["department_id"])

    op.create_table(
        "ticket_subcategories",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_ticket_subcategories"),
        sa.UniqueConstraint("category_id", "name", name="uq_ticket_subcategories_category_name"),
        sa.ForeignKeyConstraint(
            ["category_id"], ["ticket_categories.id"],
            name="fk_ticket_subcategories_category_id_ticket_categories", ondelete="CASCADE",
        ),
    )
    op.create_index("ix_ticket_subcategories_category_id", "ticket_subcategories", ["category_id"])

    op.create_table(
        "providers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("department_ids", sa.JSON(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_providers"),
        sa.UniqueConstraint("name", name="uq_providers_name"),
    )

    op.create_table(
        "gerencia_viewers",
        sa.Column("gerencia_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("gerencia_id", "user_id", name="pk_gerencia_viewers"),
        sa.ForeignKeyConstraint(
            ["gerencia_id"], ["gerencias.id"], name="fk_gerencia_viewers_gerencia_id_gerencias",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_gerencia_viewers_user_id_users", ondelete="CASCADE"
        ),
    )
    op.create_index("ix_gerencia_viewers_user_id", "gerencia_viewers", ["user_id"])

    # ------------------------------------------------------------------ columnas nuevas
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("department_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("vip", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.create_foreign_key(
            "fk_users_department_id_departments", "departments", ["department_id"], ["id"],
            ondelete="SET NULL",
        )
        batch.create_index("ix_users_department_id", ["department_id"])

    with op.batch_alter_table("ticket_types") as batch:
        batch.add_column(sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()))

    with op.batch_alter_table("tickets") as batch:
        batch.add_column(sa.Column("category_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("subcategory_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("provider_id", sa.Integer(), nullable=True))
        batch.add_column(
            sa.Column("channel", sa.String(20), nullable=False, server_default=sa.text("'portal'"))
        )
        batch.add_column(sa.Column("vip", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column("resolution_note", sa.Text(), nullable=True))
        batch.add_column(sa.Column("resolved_by_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("requester_department_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("parent_ticket_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("business_resolution_minutes", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_tickets_category_id_ticket_categories", "ticket_categories", ["category_id"], ["id"]
        )
        batch.create_foreign_key(
            "fk_tickets_subcategory_id_ticket_subcategories", "ticket_subcategories",
            ["subcategory_id"], ["id"],
        )
        batch.create_foreign_key("fk_tickets_provider_id_providers", "providers", ["provider_id"], ["id"])
        batch.create_foreign_key("fk_tickets_resolved_by_id_users", "users", ["resolved_by_id"], ["id"])
        batch.create_foreign_key(
            "fk_tickets_requester_department_id_departments", "departments",
            ["requester_department_id"], ["id"], ondelete="SET NULL",
        )
        batch.create_foreign_key(
            "fk_tickets_parent_ticket_id_tickets", "tickets", ["parent_ticket_id"], ["id"],
            ondelete="SET NULL",
        )
        batch.create_check_constraint(
            op.f("ck_tickets_channel_valid"), "channel IN ('portal', 'email', 'phone', 'other')"
        )
        for column in TICKET_INDEXED:
            batch.create_index(f"ix_tickets_{column}", [column])

    with op.batch_alter_table("sla_policies") as batch:
        batch.add_column(sa.Column("category_ids", sa.JSON(), nullable=False, server_default=EMPTY_JSON_LIST))
        batch.add_column(
            sa.Column("subcategory_ids", sa.JSON(), nullable=False, server_default=EMPTY_JSON_LIST)
        )
        batch.add_column(sa.Column("vip_only", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("sla_policies") as batch:
        batch.drop_column("vip_only")
        batch.drop_column("subcategory_ids")
        batch.drop_column("category_ids")

    with op.batch_alter_table("tickets") as batch:
        for column in TICKET_INDEXED:
            batch.drop_index(f"ix_tickets_{column}")
        batch.drop_constraint(op.f("ck_tickets_channel_valid"), type_="check")
        batch.drop_constraint("fk_tickets_parent_ticket_id_tickets", type_="foreignkey")
        batch.drop_constraint("fk_tickets_requester_department_id_departments", type_="foreignkey")
        batch.drop_constraint("fk_tickets_resolved_by_id_users", type_="foreignkey")
        batch.drop_constraint("fk_tickets_provider_id_providers", type_="foreignkey")
        batch.drop_constraint("fk_tickets_subcategory_id_ticket_subcategories", type_="foreignkey")
        batch.drop_constraint("fk_tickets_category_id_ticket_categories", type_="foreignkey")
        for column in (
            "business_resolution_minutes", "parent_ticket_id", "requester_department_id",
            "resolved_by_id", "resolution_note", "vip", "channel", "provider_id",
            "subcategory_id", "category_id",
        ):
            batch.drop_column(column)

    with op.batch_alter_table("ticket_types") as batch:
        batch.drop_column("active")

    with op.batch_alter_table("users") as batch:
        batch.drop_index("ix_users_department_id")
        batch.drop_constraint("fk_users_department_id_departments", type_="foreignkey")
        batch.drop_column("vip")
        batch.drop_column("department_id")

    op.drop_index("ix_gerencia_viewers_user_id", table_name="gerencia_viewers")
    op.drop_table("gerencia_viewers")
    op.drop_table("providers")
    op.drop_index("ix_ticket_subcategories_category_id", table_name="ticket_subcategories")
    op.drop_table("ticket_subcategories")
    op.drop_index("ix_ticket_categories_department_id", table_name="ticket_categories")
    op.drop_table("ticket_categories")
    op.drop_index("ix_department_aliases_department_id", table_name="department_aliases")
    op.drop_table("department_aliases")

    with op.batch_alter_table("departments") as batch:
        batch.drop_index("ix_departments_gerencia_id")
        batch.drop_constraint("fk_departments_gerencia_id_gerencias", type_="foreignkey")
        batch.drop_column("active")
        batch.drop_column("serves_tickets")
        batch.drop_column("gerencia_id")

    op.drop_table("gerencias")
