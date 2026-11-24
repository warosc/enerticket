"""Tablas core de la Fase 2 y renombrado de tablas legacy de la Fase 1.

Revision ID: 0001_core
Revises:
Create Date: 2026-11-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_core"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Tablas creadas por la Fase 1 con Base.metadata.create_all().
LEGACY_TABLES = ("tickets", "comments", "audit_events")
LEGACY_PREFIX = "legacy_"

CORE_TABLES_DROP_ORDER = (
    "ticket_history",
    "ticket_comments",
    "tickets",
    "workflow_transitions",
    "workflow_states",
    "workflow_definitions",
    "priorities",
    "ticket_types",
    "projects",
    "team_members",
    "teams",
    "departments",
    "users",
)


def _ts(name: str, nullable: bool = True) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


# --------------------------------------------------------------------------- legacy


def _rename_postgres_objects(bind: sa.engine.Connection, table: str, old_prefix: str, new_prefix: str) -> None:
    """Renombra índices (incluida la PK) y secuencias asociadas a ``table`` en PostgreSQL.

    Evita conflictos de nombres (``tickets_pkey``, ``tickets_id_seq``) con las tablas nuevas.
    """
    indexes = bind.execute(
        sa.text(
            "SELECT indexname FROM pg_indexes "
            "WHERE schemaname = current_schema() AND tablename = :table"
        ),
        {"table": table},
    ).scalars().all()
    for index in indexes:
        if index.startswith(old_prefix):
            op.execute(f'ALTER INDEX "{index}" RENAME TO "{new_prefix}{index[len(old_prefix):]}"')
    sequences = bind.execute(
        sa.text(
            "SELECT s.relname FROM pg_class s "
            "JOIN pg_depend d ON d.objid = s.oid "
            "JOIN pg_class t ON d.refobjid = t.oid "
            "JOIN pg_namespace n ON n.oid = t.relnamespace "
            "WHERE s.relkind = 'S' AND t.relname = :table AND n.nspname = current_schema()"
        ),
        {"table": table},
    ).scalars().all()
    for sequence in sequences:
        if sequence.startswith(old_prefix):
            op.execute(
                f'ALTER SEQUENCE "{sequence}" RENAME TO "{new_prefix}{sequence[len(old_prefix):]}"'
            )


def _rename_legacy_tables() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = set(inspector.get_table_names())
    if "tickets" in existing:
        columns = {c["name"] for c in inspector.get_columns("tickets")}
        if "status_id" in columns:
            raise RuntimeError(
                "La tabla 'tickets' ya tiene el esquema de la Fase 2 pero la base no tiene "
                "historial de Alembic. Verifique el estado y use 'alembic stamp 0001_core' si procede."
            )
    for table in LEGACY_TABLES:
        if table not in existing:
            continue
        target = f"{LEGACY_PREFIX}{table}"
        if target in existing:
            raise RuntimeError(f"No se puede renombrar '{table}': ya existe '{target}'")
        op.rename_table(table, target)
        if bind.dialect.name == "postgresql":
            _rename_postgres_objects(bind, target, f"{table}_", f"{LEGACY_PREFIX}{table}_")


def _restore_legacy_tables() -> None:
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())
    for table in LEGACY_TABLES:
        source = f"{LEGACY_PREFIX}{table}"
        if source in existing and table not in existing:
            op.rename_table(source, table)
            if bind.dialect.name == "postgresql":
                _rename_postgres_objects(bind, table, f"{LEGACY_PREFIX}{table}_", f"{table}_")


# --------------------------------------------------------------------------- upgrade / downgrade


def upgrade() -> None:
    _rename_legacy_tables()

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("keycloak_sub", sa.String(255), nullable=False),
        sa.Column("username", sa.String(150), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("roles", sa.JSON(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        _ts("created_at", nullable=False),
        _ts("last_login_at"),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("keycloak_sub", name="uq_users_keycloak_sub"),
    )
    op.create_index("ix_users_username", "users", ["username"])

    op.create_table(
        "departments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_departments"),
        sa.UniqueConstraint("name", name="uq_departments_name"),
        sa.ForeignKeyConstraint(
            ["parent_id"], ["departments.id"],
            name="fk_departments_parent_id_departments", ondelete="SET NULL",
        ),
    )

    op.create_table(
        "teams",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("department_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("external_group", sa.String(255), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_teams"),
        sa.UniqueConstraint("department_id", "name", name="uq_teams_department_name"),
        sa.ForeignKeyConstraint(
            ["department_id"], ["departments.id"], name="fk_teams_department_id_departments"
        ),
    )
    op.create_index("ix_teams_department_id", "teams", ["department_id"])
    op.create_index("ix_teams_external_group", "teams", ["external_group"])

    op.create_table(
        "team_members",
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.PrimaryKeyConstraint("team_id", "user_id", name="pk_team_members"),
        sa.ForeignKeyConstraint(
            ["team_id"], ["teams.id"], name="fk_team_members_team_id_teams", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_team_members_user_id_users", ondelete="CASCADE"
        ),
        sa.CheckConstraint("role IN ('member', 'lead')", name="ck_team_members_role_valid"),
    )

    op.create_table(
        "projects",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(30), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=True),
        sa.Column("team_id", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_projects"),
        sa.UniqueConstraint("code", name="uq_projects_code"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name="fk_projects_owner_id_users"),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], name="fk_projects_team_id_teams"),
    )

    op.create_table(
        "ticket_types",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(50), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_ticket_types"),
        sa.UniqueConstraint("key", name="uq_ticket_types_key"),
    )

    op.create_table(
        "priorities",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(30), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_priorities"),
        sa.UniqueConstraint("key", name="uq_priorities_key"),
    )

    op.create_table(
        "workflow_definitions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(50), nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_workflow_definitions"),
        sa.UniqueConstraint("key", name="uq_workflow_definitions_key"),
    )

    op.create_table(
        "workflow_states",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("workflow_id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(50), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("category", sa.String(20), nullable=False),
        sa.Column("order", sa.Integer(), nullable=False),
        sa.Column("is_initial", sa.Boolean(), nullable=False),
        sa.Column("is_final", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_workflow_states"),
        sa.UniqueConstraint("workflow_id", "key", name="uq_workflow_states_workflow_key"),
        sa.ForeignKeyConstraint(
            ["workflow_id"], ["workflow_definitions.id"],
            name="fk_workflow_states_workflow_id_workflow_definitions",
        ),
        sa.CheckConstraint(
            "category IN ('todo', 'in_progress', 'waiting', 'done')",
            name="ck_workflow_states_category_valid",
        ),
    )
    op.create_index("ix_workflow_states_workflow_id", "workflow_states", ["workflow_id"])

    op.create_table(
        "workflow_transitions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("workflow_id", sa.Integer(), nullable=False),
        sa.Column("from_state_id", sa.Integer(), nullable=False),
        sa.Column("to_state_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("requires_approval", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_workflow_transitions"),
        sa.UniqueConstraint(
            "workflow_id", "from_state_id", "to_state_id", name="uq_workflow_transitions_path"
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id"], ["workflow_definitions.id"],
            name="fk_workflow_transitions_workflow_id_workflow_definitions",
        ),
        sa.ForeignKeyConstraint(
            ["from_state_id"], ["workflow_states.id"],
            name="fk_workflow_transitions_from_state_id_workflow_states",
        ),
        sa.ForeignKeyConstraint(
            ["to_state_id"], ["workflow_states.id"],
            name="fk_workflow_transitions_to_state_id_workflow_states",
        ),
    )
    op.create_index("ix_workflow_transitions_workflow_id", "workflow_transitions", ["workflow_id"])

    op.create_table(
        "tickets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("public_key", sa.String(30), nullable=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("type_id", sa.Integer(), nullable=False),
        sa.Column("priority_id", sa.Integer(), nullable=False),
        sa.Column("status_id", sa.Integer(), nullable=False),
        sa.Column("department_id", sa.Integer(), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=True),
        sa.Column("requester_id", sa.Integer(), nullable=False),
        sa.Column("assignee_id", sa.Integer(), nullable=True),
        _ts("created_at", nullable=False),
        _ts("updated_at", nullable=False),
        _ts("first_response_at"),
        _ts("resolved_at"),
        _ts("closed_at"),
        sa.PrimaryKeyConstraint("id", name="pk_tickets"),
        sa.UniqueConstraint("public_key", name="uq_tickets_public_key"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name="fk_tickets_project_id_projects"),
        sa.ForeignKeyConstraint(["type_id"], ["ticket_types.id"], name="fk_tickets_type_id_ticket_types"),
        sa.ForeignKeyConstraint(["priority_id"], ["priorities.id"], name="fk_tickets_priority_id_priorities"),
        sa.ForeignKeyConstraint(
            ["status_id"], ["workflow_states.id"], name="fk_tickets_status_id_workflow_states"
        ),
        sa.ForeignKeyConstraint(
            ["department_id"], ["departments.id"], name="fk_tickets_department_id_departments"
        ),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], name="fk_tickets_team_id_teams"),
        sa.ForeignKeyConstraint(["requester_id"], ["users.id"], name="fk_tickets_requester_id_users"),
        sa.ForeignKeyConstraint(["assignee_id"], ["users.id"], name="fk_tickets_assignee_id_users"),
    )
    for column in ("status_id", "department_id", "team_id", "requester_id", "assignee_id", "created_at"):
        op.create_index(f"ix_tickets_{column}", "tickets", [column])

    op.create_table(
        "ticket_comments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ticket_id", sa.Integer(), nullable=False),
        sa.Column("author_id", sa.Integer(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("visibility", sa.String(20), nullable=False),
        _ts("created_at", nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_ticket_comments"),
        sa.ForeignKeyConstraint(
            ["ticket_id"], ["tickets.id"], name="fk_ticket_comments_ticket_id_tickets", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["author_id"], ["users.id"], name="fk_ticket_comments_author_id_users"),
        sa.CheckConstraint(
            "visibility IN ('public', 'internal')", name="ck_ticket_comments_visibility_valid"
        ),
    )
    op.create_index("ix_ticket_comments_ticket_id", "ticket_comments", ["ticket_id"])

    op.create_table(
        "ticket_history",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ticket_id", sa.Integer(), nullable=False),
        sa.Column("actor_id", sa.Integer(), nullable=True),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("before_json", sa.JSON(), nullable=True),
        sa.Column("after_json", sa.JSON(), nullable=True),
        _ts("timestamp", nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_ticket_history"),
        sa.ForeignKeyConstraint(
            ["ticket_id"], ["tickets.id"], name="fk_ticket_history_ticket_id_tickets", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], name="fk_ticket_history_actor_id_users"),
    )
    op.create_index("ix_ticket_history_ticket_id", "ticket_history", ["ticket_id"])
    op.create_index("ix_ticket_history_event_type", "ticket_history", ["event_type"])
    op.create_index("ix_ticket_history_timestamp", "ticket_history", ["timestamp"])


def downgrade() -> None:
    for table in CORE_TABLES_DROP_ORDER:
        op.drop_table(table)
    _restore_legacy_tables()
