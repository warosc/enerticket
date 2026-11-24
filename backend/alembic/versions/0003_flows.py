"""Flujos: aprobaciones, automatizaciones y notificaciones.

Revision ID: 0003_flows
Revises: 0002_sla
Create Date: 2026-11-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_flows"
down_revision: str | None = "0002_sla"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FLOWS_TABLES_DROP_ORDER = (
    "notifications",
    "automation_executions",
    "automation_rules",
    "approval_decisions",
    "approval_steps",
    "approval_requests",
    "approval_policies",
)


def _ts(name: str, nullable: bool = True) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def upgrade() -> None:
    op.create_table(
        "approval_policies",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("transition_id", sa.Integer(), nullable=False),
        sa.Column("ticket_type_keys", sa.JSON(), nullable=False),
        sa.Column("department_ids", sa.JSON(), nullable=False),
        sa.Column("steps", sa.JSON(), nullable=False),
        sa.Column("order", sa.Integer(), nullable=False),
        _ts("created_at", nullable=False),
        _ts("updated_at", nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_approval_policies"),
        sa.ForeignKeyConstraint(
            ["transition_id"], ["workflow_transitions.id"],
            name="fk_approval_policies_transition_id_workflow_transitions", ondelete="CASCADE",
        ),
    )
    op.create_index("ix_approval_policies_transition_id", "approval_policies", ["transition_id"])

    op.create_table(
        "approval_requests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ticket_id", sa.Integer(), nullable=False),
        sa.Column("transition_id", sa.Integer(), nullable=False),
        sa.Column("policy_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("requested_by_id", sa.Integer(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        _ts("created_at", nullable=False),
        _ts("decided_at"),
        _ts("executed_at"),
        sa.Column("execution_error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_approval_requests"),
        sa.ForeignKeyConstraint(
            ["ticket_id"], ["tickets.id"],
            name="fk_approval_requests_ticket_id_tickets", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["transition_id"], ["workflow_transitions.id"],
            name="fk_approval_requests_transition_id_workflow_transitions", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["policy_id"], ["approval_policies.id"],
            name="fk_approval_requests_policy_id_approval_policies", ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_id"], ["users.id"],
            name="fk_approval_requests_requested_by_id_users", ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'cancelled')",
            name=op.f("ck_approval_requests_status_valid"),
        ),
    )
    op.create_index("ix_approval_requests_status", "approval_requests", ["status"])
    op.create_index("ix_approval_requests_created_at", "approval_requests", ["created_at"])
    op.create_index(
        "ix_approval_requests_ticket_status", "approval_requests", ["ticket_id", "status"]
    )

    op.create_table(
        "approval_steps",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.Integer(), nullable=False),
        sa.Column("order", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("approver_type", sa.String(20), nullable=False),
        sa.Column("approver_user_id", sa.Integer(), nullable=True),
        sa.Column("approver_role", sa.String(50), nullable=True),
        sa.Column("approver_team_id", sa.Integer(), nullable=True),
        sa.Column("approver_department_id", sa.Integer(), nullable=True),
        sa.Column("rule", sa.String(10), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        _ts("decided_at"),
        sa.PrimaryKeyConstraint("id", name="pk_approval_steps"),
        sa.ForeignKeyConstraint(
            ["request_id"], ["approval_requests.id"],
            name="fk_approval_steps_request_id_approval_requests", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["approver_user_id"], ["users.id"],
            name="fk_approval_steps_approver_user_id_users", ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["approver_team_id"], ["teams.id"],
            name="fk_approval_steps_approver_team_id_teams", ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["approver_department_id"], ["departments.id"],
            name="fk_approval_steps_approver_department_id_departments", ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "status IN ('waiting', 'pending', 'approved', 'rejected', 'cancelled')",
            name=op.f("ck_approval_steps_status_valid"),
        ),
        sa.CheckConstraint("rule IN ('all', 'any')", name=op.f("ck_approval_steps_rule_valid")),
        sa.CheckConstraint(
            "approver_type IN ('user', 'role', 'team', 'team_lead')",
            name=op.f("ck_approval_steps_approver_type_valid"),
        ),
    )
    op.create_index("ix_approval_steps_request_id", "approval_steps", ["request_id"])

    op.create_table(
        "approval_decisions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("step_id", sa.Integer(), nullable=False),
        sa.Column("approver_id", sa.Integer(), nullable=False),
        sa.Column("decision", sa.String(20), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("is_override", sa.Boolean(), nullable=False),
        _ts("created_at", nullable=False),
        _ts("decided_at"),
        sa.PrimaryKeyConstraint("id", name="pk_approval_decisions"),
        sa.ForeignKeyConstraint(
            ["step_id"], ["approval_steps.id"],
            name="fk_approval_decisions_step_id_approval_steps", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["approver_id"], ["users.id"],
            name="fk_approval_decisions_approver_id_users", ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "decision IN ('pending', 'approved', 'rejected', 'skipped')",
            name=op.f("ck_approval_decisions_decision_valid"),
        ),
    )
    op.create_index("ix_approval_decisions_step_id", "approval_decisions", ["step_id"])
    op.create_index(
        "ix_approval_decisions_approver_decision",
        "approval_decisions",
        ["approver_id", "decision"],
    )

    op.create_table(
        "automation_rules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("trigger", sa.String(80), nullable=False),
        sa.Column("conditions", sa.JSON(), nullable=False),
        sa.Column("actions", sa.JSON(), nullable=False),
        sa.Column("order", sa.Integer(), nullable=False),
        sa.Column("stop_processing", sa.Boolean(), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        _ts("created_at", nullable=False),
        _ts("updated_at", nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_automation_rules"),
        sa.ForeignKeyConstraint(
            ["created_by_id"], ["users.id"],
            name="fk_automation_rules_created_by_id_users", ondelete="SET NULL",
        ),
    )
    op.create_index("ix_automation_rules_trigger", "automation_rules", ["trigger"])

    op.create_table(
        "automation_executions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("rule_id", sa.Integer(), nullable=False),
        sa.Column("ticket_id", sa.Integer(), nullable=True),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("history_id", sa.Integer(), nullable=True),
        sa.Column("depth", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        _ts("created_at", nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_automation_executions"),
        sa.ForeignKeyConstraint(
            ["rule_id"], ["automation_rules.id"],
            name="fk_automation_executions_rule_id_automation_rules", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["ticket_id"], ["tickets.id"],
            name="fk_automation_executions_ticket_id_tickets", ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "status IN ('success', 'partial', 'error')",
            name=op.f("ck_automation_executions_status_valid"),
        ),
    )
    op.create_index("ix_automation_executions_rule_id", "automation_executions", ["rule_id"])
    op.create_index("ix_automation_executions_ticket_id", "automation_executions", ["ticket_id"])
    op.create_index(
        "ix_automation_executions_created_at", "automation_executions", ["created_at"]
    )

    op.create_table(
        "notifications",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(50), nullable=False),
        sa.Column("title", sa.String(250), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("ticket_id", sa.Integer(), nullable=True),
        sa.Column("data", sa.JSON(), nullable=True),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("is_read", sa.Boolean(), nullable=False),
        _ts("read_at"),
        sa.Column("delivery_status", sa.String(20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        _ts("sent_at"),
        _ts("created_at", nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_notifications"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_notifications_user_id_users", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["ticket_id"], ["tickets.id"],
            name="fk_notifications_ticket_id_tickets", ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "channel IN ('in_app', 'email')", name=op.f("ck_notifications_channel_valid")
        ),
        sa.CheckConstraint(
            "delivery_status IN ('delivered', 'pending', 'sent', 'failed', 'skipped')",
            name=op.f("ck_notifications_delivery_status_valid"),
        ),
    )
    op.create_index("ix_notifications_ticket_id", "notifications", ["ticket_id"])
    op.create_index("ix_notifications_created_at", "notifications", ["created_at"])
    op.create_index("ix_notifications_inbox", "notifications", ["user_id", "channel", "is_read"])
    op.create_index("ix_notifications_delivery", "notifications", ["channel", "delivery_status"])


def downgrade() -> None:
    for table in FLOWS_TABLES_DROP_ORDER:
        op.drop_table(table)
