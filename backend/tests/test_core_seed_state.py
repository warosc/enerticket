"""Seeds de «una sola vez»: lo que el administrador borra o renombra no reaparece al reiniciar."""

from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa
from alembic import command
from sqlalchemy import func, select

from app.core.db import Base, SessionLocal
from app.models.approvals import ApprovalPolicy
from app.models.automation import AutomationRule
from app.models.core import Department, WorkflowTransition
from app.models.seed_state import SeedState
from app.models.sla import BusinessCalendar, CalendarHoliday, SlaPolicy
from app.seeds import run_all
from tests.test_core_migrations import alembic_config


def _count(session, model, *where) -> int:
    stmt = select(func.count()).select_from(model)
    if where:
        stmt = stmt.where(*where)
    return session.scalar(stmt) or 0


def test_seeds_do_not_restore_deleted_or_renamed_configuration(monkeypatch):
    monkeypatch.setenv("SEED_FLOWS_EXAMPLES", "true")
    run_all()
    with SessionLocal() as session:
        assert _count(session, ApprovalPolicy) == 3
        assert _count(session, AutomationRule) >= 2
        assert {s.key for s in session.scalars(select(SeedState)).all()} >= {
            "core.departments.v1",
            "sla.defaults.v1",
            "flows.examples.v1",
        }
        # El administrador quita las aprobaciones de ejemplo, las reglas, renombra una política
        # SLA y un departamento.
        for policy in session.scalars(select(ApprovalPolicy)).all():
            session.delete(policy)
        for transition in session.scalars(select(WorkflowTransition)).all():
            transition.requires_approval = False
        for rule in session.scalars(select(AutomationRule)).all():
            session.delete(rule)
        critical = session.scalar(select(SlaPolicy).where(SlaPolicy.name == "Crítica 24x7"))
        critical.name = "Crítica (renombrada)"
        session.scalar(select(Department).where(Department.name == "General")).name = "Mesa general"
        calendar = session.scalar(select(BusinessCalendar).where(BusinessCalendar.name == "Horario hábil Guatemala"))
        calendar.name = "Hábil GT"
        holidays_before = _count(session, CalendarHoliday, CalendarHoliday.calendar_id == calendar.id)
        session.commit()

    run_all()  # «reinicio» del contenedor api

    with SessionLocal() as session:
        assert _count(session, ApprovalPolicy) == 0
        assert _count(session, AutomationRule) == 0
        assert _count(session, WorkflowTransition, WorkflowTransition.requires_approval.is_(True)) == 0
        assert _count(session, SlaPolicy, SlaPolicy.name == "Crítica 24x7") == 0
        assert _count(session, SlaPolicy) == 4
        assert _count(session, Department, Department.name == "General") == 0
        assert _count(session, BusinessCalendar) == 2
        # Los feriados se siguen manteniendo por id aunque el calendario se haya renombrado.
        renamed = session.scalar(select(BusinessCalendar).where(BusinessCalendar.name == "Hábil GT"))
        assert _count(session, CalendarHoliday, CalendarHoliday.calendar_id == renamed.id) == holidays_before > 0


def test_existing_configuration_is_adopted_without_creating_defaults():
    """Base anterior a seed_state: no se crean políticas SLA si ya había alguna."""
    with SessionLocal() as session:
        calendar = BusinessCalendar(name="Propio", description=None, timezone="America/Guatemala", windows=[])
        session.add(calendar)
        session.commit()
    run_all()
    with SessionLocal() as session:
        assert _count(session, SlaPolicy) == 0
        assert [c.name for c in session.scalars(select(BusinessCalendar)).all()] == ["Propio"]
        assert session.get(SeedState, "sla.defaults.v1") is not None


def test_alembic_head_creates_every_model_table(tmp_path: Path):
    url = f"sqlite:///{(tmp_path / 'head.db').as_posix()}"
    command.upgrade(alembic_config(url), "head")
    engine = sa.create_engine(url)
    try:
        inspector = sa.inspect(engine)
        tables = set(inspector.get_table_names())
        assert set(Base.metadata.tables) <= tables
        columns = {c["name"] for c in inspector.get_columns("seed_state")}
        assert columns == set(Base.metadata.tables["seed_state"].columns.keys())
    finally:
        engine.dispose()
    command.downgrade(alembic_config(url), "0004_attachments")
    engine = sa.create_engine(url)
    try:
        assert "seed_state" not in sa.inspect(engine).get_table_names()
    finally:
        engine.dispose()
