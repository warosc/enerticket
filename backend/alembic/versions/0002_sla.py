"""Tablas del módulo SLA: calendarios hábiles, feriados, políticas, instancias y eventos.

Revision ID: 0002_sla
Revises: 0001_core
Create Date: 2026-11-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_sla"
down_revision: str | None = "0001_core"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ts(name: str, nullable: bool = True) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def upgrade() -> None:
    op.create_table(
        "business_calendars",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("windows", sa.JSON(), nullable=False),
        _ts("created_at", nullable=False),
        _ts("updated_at", nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_business_calendars"),
        sa.UniqueConstraint("name", name="uq_business_calendars_name"),
    )

    op.create_table(
        "calendar_holidays",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("calendar_id", sa.Integer(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("recurring", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_calendar_holidays"),
        sa.UniqueConstraint("calendar_id", "date", name="uq_calendar_holidays_calendar_date"),
        sa.ForeignKeyConstraint(
            ["calendar_id"], ["business_calendars.id"],
            name="fk_calendar_holidays_calendar_id_business_calendars", ondelete="CASCADE",
        ),
    )
    op.create_index("ix_calendar_holidays_calendar_id", "calendar_holidays", ["calendar_id"])

    op.create_table(
        "sla_policies",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("calendar_id", sa.Integer(), nullable=False),
        sa.Column("priority_keys", sa.JSON(), nullable=False),
        sa.Column("type_keys", sa.JSON(), nullable=False),
        sa.Column("department_ids", sa.JSON(), nullable=False),
        sa.Column("precedence", sa.Integer(), nullable=False),
        sa.Column("first_response_minutes", sa.Integer(), nullable=True),
        sa.Column("resolution_minutes", sa.Integer(), nullable=True),
        sa.Column("warning_threshold_pct", sa.Integer(), nullable=False),
        sa.Column("pause_categories", sa.JSON(), nullable=False),
        sa.Column("pause_states", sa.JSON(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        _ts("created_at", nullable=False),
        _ts("updated_at", nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_sla_policies"),
        sa.UniqueConstraint("name", name="uq_sla_policies_name"),
        sa.ForeignKeyConstraint(
            ["calendar_id"], ["business_calendars.id"], name="fk_sla_policies_calendar_id_business_calendars"
        ),
        sa.CheckConstraint(
            "warning_threshold_pct >= 1 AND warning_threshold_pct <= 99",
            name=op.f("ck_sla_policies_warning_threshold_range"),
        ),
    )
    op.create_index("ix_sla_policies_calendar_id", "sla_policies", ["calendar_id"])

    op.create_table(
        "sla_instances",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ticket_id", sa.Integer(), nullable=False),
        sa.Column("policy_id", sa.Integer(), nullable=False),
        sa.Column("calendar_id", sa.Integer(), nullable=False),
        sa.Column("metric", sa.String(30), nullable=False),
        sa.Column("target_minutes", sa.Integer(), nullable=False),
        sa.Column("warning_threshold_pct", sa.Integer(), nullable=False),
        _ts("started_at", nullable=False),
        _ts("target_at"),
        _ts("warning_at"),
        _ts("paused_at"),
        sa.Column("paused_business_seconds", sa.Integer(), nullable=False),
        _ts("stopped_at"),
        _ts("breached_at"),
        _ts("warned_at"),
        sa.Column("status", sa.String(20), nullable=False),
        _ts("created_at", nullable=False),
        _ts("updated_at", nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_sla_instances"),
        sa.ForeignKeyConstraint(
            ["ticket_id"], ["tickets.id"], name="fk_sla_instances_ticket_id_tickets", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["policy_id"], ["sla_policies.id"], name="fk_sla_instances_policy_id_sla_policies"),
        sa.ForeignKeyConstraint(
            ["calendar_id"], ["business_calendars.id"], name="fk_sla_instances_calendar_id_business_calendars"
        ),
        sa.CheckConstraint("metric IN ('first_response', 'resolution')", name=op.f("ck_sla_instances_metric_valid")),
        sa.CheckConstraint(
            "status IN ('running', 'paused', 'met', 'breached', 'cancelled')", name=op.f("ck_sla_instances_status_valid")
        ),
    )
    op.create_index("ix_sla_instances_policy_id", "sla_instances", ["policy_id"])
    op.create_index("ix_sla_instances_ticket_metric", "sla_instances", ["ticket_id", "metric"])
    op.create_index("ix_sla_instances_status_target", "sla_instances", ["status", "target_at"])
    op.create_index("ix_sla_instances_status_warning", "sla_instances", ["status", "warning_at"])

    op.create_table(
        "sla_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("instance_id", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(40), nullable=False),
        _ts("at", nullable=False),
        sa.Column("data", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_sla_events"),
        sa.ForeignKeyConstraint(
            ["instance_id"], ["sla_instances.id"], name="fk_sla_events_instance_id_sla_instances", ondelete="CASCADE"
        ),
    )
    op.create_index("ix_sla_events_instance_id", "sla_events", ["instance_id"])


def downgrade() -> None:
    op.drop_index("ix_sla_events_instance_id", table_name="sla_events")
    op.drop_table("sla_events")
    for index in (
        "ix_sla_instances_status_warning",
        "ix_sla_instances_status_target",
        "ix_sla_instances_ticket_metric",
        "ix_sla_instances_policy_id",
    ):
        op.drop_index(index, table_name="sla_instances")
    op.drop_table("sla_instances")
    op.drop_index("ix_sla_policies_calendar_id", table_name="sla_policies")
    op.drop_table("sla_policies")
    op.drop_index("ix_calendar_holidays_calendar_id", table_name="calendar_holidays")
    op.drop_table("calendar_holidays")
    op.drop_table("business_calendars")
