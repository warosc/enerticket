"""Plantillas de reportes: permisos, compartir, duplicar, programar, tarea programada y migración."""

from __future__ import annotations

import importlib
import io
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from openpyxl import load_workbook
from sqlalchemy import select

from app.core.config import settings
from app.core.db import Base, SessionLocal, utcnow
from app.models.reports import ReportTemplate
from app.services.notifications.email import set_smtp_factory
from app.services.reports.templates import next_run_at, run_due_templates
from tests.conftest import DEPARTMENT_SOC, DEPARTMENT_TELECOM
from tests.test_reports_builder import report_data  # noqa: F401  (fixture compartida)

BACKEND_DIR = Path(__file__).resolve().parents[1]
FACTORY_NAMES = {
    "Informe mensual de proveedor",
    "Desempeño por técnico",
    "Tickets por día",
    "Tickets por gerencia solicitante",
    "Cumplimiento de SLA por categoría",
}
DEFINITION = {
    "title": "Mis tickets",
    "period": "custom",
    "date_from": "2026-11-01",
    "date_to": "2026-11-07",
    "sections": [{"title": "Por tipo", "rows": ["type"], "metrics": ["count"]}],
}
SCHEDULE = {"enabled": True, "frequency": "monthly", "day": 1, "hour": 7, "recipients": ["jefe@energuate.com"]}


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


@pytest.fixture
def factory_templates() -> None:
    seed = importlib.import_module("app.seeds.40_report_templates")
    with SessionLocal() as session:
        seed.run(session)
        session.commit()


@pytest.fixture
def lead(make_user):
    return make_user("lider.telecom", roles=("agent",), departments=(DEPARTMENT_TELECOM,), team_role="lead")


def create(client, persona, **overrides: Any):
    body = {"name": "Mi plantilla", "definition": DEFINITION, **overrides}
    return client.post("/reports/templates", json=body, headers=persona.headers)


def names(client, persona) -> set[str]:
    response = client.get("/reports/templates", headers=persona.headers)
    assert response.status_code == 200
    return {t["name"] for t in response.json()}


# --------------------------------------------------------------------------- seed de fábrica


def test_factory_seed_idempotent_and_runnable(client, admin, factory_templates):
    seed = importlib.import_module("app.seeds.40_report_templates")
    with SessionLocal() as session:
        seed.run(session)
        session.commit()
        templates = session.scalars(select(ReportTemplate).where(ReportTemplate.is_system.is_(True))).all()
    assert {t.name for t in templates} == FACTORY_NAMES and len(templates) == 5
    provider = next(t for t in templates if t.name == "Informe mensual de proveedor")
    assert provider.definition["period"] == "last_month"
    assert provider.definition["date_field"] == "resolved_at"
    assert [s["filters"]["type_keys"] for s in provider.definition["sections"]] == [
        ["incident"], ["service_request"], ["work_order"],
    ]
    for template in templates:
        response = client.post(f"/reports/templates/{template.id}/run", headers=admin.headers)
        assert response.status_code == 200, (template.name, response.text)


def test_factory_templates_visible_to_everyone_but_read_only(client, requester, admin, factory_templates):
    listed = client.get("/reports/templates", headers=requester.headers).json()
    assert {t["name"] for t in listed} == FACTORY_NAMES
    template = listed[0]
    assert template["is_system"] and template["can_edit"] is False and template["owner"] is None
    for persona in (requester, admin):
        patch = client.patch(f"/reports/templates/{template['id']}", json={"name": "X"}, headers=persona.headers)
        assert patch.status_code == 403
        assert "duplíquela" in patch.json()["detail"]
        assert client.delete(f"/reports/templates/{template['id']}", headers=persona.headers).status_code == 403

    copy = client.post(f"/reports/templates/{template['id']}/duplicate", headers=requester.headers)
    assert copy.status_code == 201
    body = copy.json()
    assert body["is_system"] is False and body["owner"]["id"] == requester.id and body["can_edit"]
    assert body["name"] == f"{template['name']} (copia)"
    assert body["definition"] == template["definition"]
    renamed = client.patch(f"/reports/templates/{body['id']}", json={"name": "Mi copia"}, headers=requester.headers)
    assert renamed.status_code == 200 and renamed.json()["name"] == "Mi copia"


# --------------------------------------------------------------------------- CRUD y compartir


def test_crud_and_visibility(client, agent, other_agent, requester, admin):
    response = create(client, agent, description="Para el jefe")
    assert response.status_code == 201, response.text
    template = response.json()
    assert template["owner"] == {"id": agent.id, "display_name": "Ana Telecom"}
    assert template["definition"]["sections"][0]["rows"] == ["type"]
    assert template["schedule"] is None and template["can_edit"]

    assert "Mi plantilla" in names(client, agent)
    assert "Mi plantilla" in names(client, admin)  # admin ve todas
    assert "Mi plantilla" not in names(client, other_agent)
    assert client.get(f"/reports/templates/{template['id']}", headers=other_agent.headers).status_code == 404
    assert client.post(f"/reports/templates/{template['id']}/run", headers=requester.headers).status_code == 404

    patched = client.patch(
        f"/reports/templates/{template['id']}", json={"description": "Actualizada"}, headers=agent.headers
    )
    assert patched.status_code == 200 and patched.json()["description"] == "Actualizada"
    assert client.patch(
        f"/reports/templates/{template['id']}", json={"name": None}, headers=agent.headers
    ).status_code == 422
    # Admin puede editar y eliminar plantillas de otros.
    assert client.patch(
        f"/reports/templates/{template['id']}", json={"name": "Revisada"}, headers=admin.headers
    ).status_code == 200
    assert client.delete(f"/reports/templates/{template['id']}", headers=agent.headers).status_code == 204
    assert client.get(f"/reports/templates/{template['id']}", headers=agent.headers).status_code == 404
    assert client.delete("/reports/templates/99999", headers=admin.headers).status_code == 404


def test_validation(client, agent):
    assert create(client, agent, definition={**DEFINITION, "sections": [{"rows": ["x"], "metrics": ["count"]}]}).status_code == 422
    assert create(client, agent, shared_roles=["superuser"]).status_code == 422
    assert create(client, agent, shared_department_ids=[99999]).status_code == 422
    assert create(client, agent, name="   ").status_code == 422
    assert create(client, agent, owner_id=1).status_code == 422
    assert create(client, agent, is_system=True).status_code == 422


def test_share_by_role_and_department(client, agent, other_agent, requester, ids):
    soc = ids["departments"][DEPARTMENT_SOC]
    template = create(client, agent, shared_department_ids=[soc]).json()
    assert template["shared_department_ids"] == [soc]
    assert "Mi plantilla" in names(client, other_agent)
    assert "Mi plantilla" not in names(client, requester)
    shared = next(t for t in client.get("/reports/templates", headers=other_agent.headers).json() if t["id"] == template["id"])
    assert shared["can_edit"] is False and shared["schedule"] is None
    # Quien la recibe compartida no la puede modificar ni borrar, pero sí ejecutar y duplicar.
    assert client.patch(f"/reports/templates/{template['id']}", json={"name": "X"}, headers=other_agent.headers).status_code == 403
    assert client.delete(f"/reports/templates/{template['id']}", headers=other_agent.headers).status_code == 403
    assert client.post(f"/reports/templates/{template['id']}/run", headers=other_agent.headers).status_code == 200
    assert client.post(f"/reports/templates/{template['id']}/duplicate", headers=other_agent.headers).status_code == 201

    by_role = create(client, agent, name="Para todos", shared_roles=["requester"]).json()
    assert by_role["shared_roles"] == ["requester"]
    assert "Para todos" in names(client, requester)


def test_shared_template_runs_with_viewer_scope(client, agent, requester, other_requester, report_data):
    template = create(client, agent, shared_roles=["requester"]).json()
    totals = {}
    for persona in (agent, requester, other_requester):
        response = client.post(f"/reports/templates/{template['id']}/run", headers=persona.headers)
        assert response.status_code == 200
        totals[persona.username] = response.json()["sections"][0]["grand_total"]["count"]
    assert totals == {"ana.telecom": 4, "carlos.solicitante": 3, "sofia.solicitante": 2}


def test_run_and_export_with_period_override(client, admin, report_data):
    template = create(client, admin).json()
    response = client.post(
        f"/reports/templates/{template['id']}/run",
        params={"date_from": "2026-10-01", "date_to": "2026-11-30"},
        headers=admin.headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert (body["date_from"], body["date_to"]) == ("2026-10-01", "2026-11-30")
    assert body["sections"][0]["grand_total"]["count"] == 6
    bad = client.post(f"/reports/templates/{template['id']}/run", params={"period": "eterno"}, headers=admin.headers)
    assert bad.status_code == 422

    export = client.get(f"/reports/templates/{template['id']}/export.xlsx", headers=admin.headers)
    assert export.status_code == 200
    workbook = load_workbook(io.BytesIO(export.content))
    assert workbook.sheetnames == ["Por tipo", "Detalle"]
    assert len(list(workbook["Detalle"].iter_rows())) - 1 == 5
    no_detail = client.get(
        f"/reports/templates/{template['id']}/export.xlsx", params={"include_detail": "false"}, headers=admin.headers
    )
    assert load_workbook(io.BytesIO(no_detail.content)).sheetnames == ["Por tipo"]


# --------------------------------------------------------------------------- programación


def test_only_admin_or_lead_can_schedule(client, agent, requester, lead, admin):
    for persona, expected in ((agent, False), (requester, False), (lead, True), (admin, True)):
        meta = client.get("/reports/builder/meta", headers=persona.headers).json()
        assert meta["can_schedule"] is expected, persona.username
    for persona in (agent, requester):
        response = create(client, persona, schedule=SCHEDULE)
        assert response.status_code == 403, persona.username
        assert "líderes de equipo" in response.json()["detail"]
    # Sin habilitar se puede guardar (no programa nada).
    disabled = create(client, agent, schedule={**SCHEDULE, "enabled": False})
    assert disabled.status_code == 201 and disabled.json()["next_run_at"] is None

    for persona in (lead, admin):
        response = create(client, persona, schedule=SCHEDULE)
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["can_schedule"] is True
        assert body["schedule"]["recipients"] == ["jefe@energuate.com"]
        next_run = datetime.fromisoformat(body["next_run_at"])
        assert next_run > utcnow()
        assert next_run.astimezone(timezone(timedelta(hours=-6))).hour == 7

    # Un agente no puede activar la programación después.
    patch = client.patch(
        f"/reports/templates/{disabled.json()['id']}", json={"schedule": SCHEDULE}, headers=agent.headers
    )
    assert patch.status_code == 403


def test_schedule_validation(client, admin, monkeypatch):
    many = [f"user{i}@energuate.com" for i in range(21)]
    assert create(client, admin, schedule={**SCHEDULE, "recipients": many}).status_code == 422
    assert create(client, admin, schedule={**SCHEDULE, "recipients": ["no-es-correo"]}).status_code == 422
    assert create(client, admin, schedule={**SCHEDULE, "recipients": []}).status_code == 422
    assert create(client, admin, schedule={**SCHEDULE, "day": 31}).status_code == 422
    assert create(client, admin, schedule={**SCHEDULE, "frequency": "weekly", "day": 7}).status_code == 422
    assert create(client, admin, schedule={**SCHEDULE, "frequency": "daily"}).status_code == 422
    assert create(client, admin, schedule={**SCHEDULE, "hour": 24}).status_code == 422

    from app.core.config import settings

    monkeypatch.setattr(settings, "report_recipient_domains", "energuate.com, @distribuidora.gt")
    outside = create(client, admin, schedule={**SCHEDULE, "recipients": ["alguien@gmail.com"]})
    assert outside.status_code == 422
    inside = create(
        client, admin,
        schedule={**SCHEDULE, "recipients": ["A@Energuate.com", "b@distribuidora.gt", "a@energuate.com"]},
    )
    assert inside.status_code == 201
    assert inside.json()["schedule"]["recipients"] == ["a@energuate.com", "b@distribuidora.gt"]


def test_schedule_disable_clears_next_run(client, admin):
    template = create(client, admin, schedule=SCHEDULE).json()
    assert template["next_run_at"] is not None
    patched = client.patch(f"/reports/templates/{template['id']}", json={"schedule": None}, headers=admin.headers)
    assert patched.status_code == 200
    assert patched.json()["schedule"] is None and patched.json()["next_run_at"] is None


@pytest.mark.parametrize(
    "schedule,now,expected",
    [
        # 24-nov-2026 09:00 Guatemala → 1-dic 07:00 Guatemala = 13:00 UTC.
        ({"frequency": "monthly", "day": 1, "hour": 7}, utc(2026, 11, 24, 15), utc(2026, 12, 1, 13)),
        # Mismo día pero antes de la hora → hoy.
        ({"frequency": "monthly", "day": 24, "hour": 18}, utc(2026, 11, 24, 15), utc(2026, 11, 25, 0)),
        # Diciembre → enero del año siguiente.
        ({"frequency": "monthly", "day": 5, "hour": 6}, utc(2026, 12, 20, 12), utc(2027, 1, 5, 12)),
        # Semanal: lunes 07:00; el 24-nov-2026 es martes → lunes 30.
        ({"frequency": "weekly", "day": 0, "hour": 7}, utc(2026, 11, 24, 15), utc(2026, 11, 30, 13)),
        # Semanal el mismo día y hora exacta → la semana siguiente.
        ({"frequency": "weekly", "day": 1, "hour": 9}, utc(2026, 11, 24, 15), utc(2026, 12, 1, 15)),
    ],
)
def test_next_run_at(schedule, now, expected):
    assert next_run_at(schedule, now) == expected


# --------------------------------------------------------------------------- tarea programada


def _due_template(client, persona, **overrides: Any) -> int:
    template = create(client, persona, schedule=SCHEDULE, **overrides).json()
    with SessionLocal() as session:
        row = session.get(ReportTemplate, template["id"])
        row.next_run_at = utcnow() - timedelta(minutes=1)
        session.commit()
    return template["id"]


def test_scheduled_task_without_smtp(client, admin, monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", "")
    template_id = _due_template(client, admin)
    now = utcnow()
    counts = run_due_templates(now)
    assert counts == {"smtp_disabled": 1}
    with SessionLocal() as session:
        row = session.get(ReportTemplate, template_id)
        assert row.last_status == "smtp_disabled"
        assert row.last_run_at == now
        assert row.next_run_at > now
    # Ya no está vencida: una segunda pasada no hace nada.
    assert run_due_templates(now) == {}


class FakeSmtp:
    sent: list[Any] = []

    def login(self, *_: Any) -> None:  # pragma: no cover - sin usuario SMTP en pruebas
        pass

    def send_message(self, message: Any) -> None:
        FakeSmtp.sent.append(message)

    def quit(self) -> None:
        pass


@pytest.fixture
def fake_smtp(monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.test")
    monkeypatch.setattr(settings, "smtp_user", "")
    FakeSmtp.sent = []
    set_smtp_factory(FakeSmtp)
    yield FakeSmtp.sent
    set_smtp_factory(None)


def test_scheduled_task_sends_xlsx_with_owner_scope(client, lead, report_data, fake_smtp):
    template_id = _due_template(client, lead)
    assert run_due_templates() == {"sent": 1}
    assert len(fake_smtp) == 1
    message = fake_smtp[0]
    assert message["To"] == "jefe@energuate.com"
    assert message["Subject"] == "Reporte programado: Mi plantilla"
    attachment = next(message.iter_attachments())
    assert attachment.get_filename().endswith(".xlsx")
    workbook = load_workbook(io.BytesIO(attachment.get_content()))
    # Alcance del dueño (líder de Telecomunicaciones): T1, T2, T3 y T6 en el periodo.
    assert len(list(workbook["Detalle"].iter_rows())) - 1 == 4
    with SessionLocal() as session:
        assert session.get(ReportTemplate, template_id).last_status == "sent"


def test_scheduled_task_skips_owner_without_rights(client, lead, fake_smtp, db):
    template_id = _due_template(client, lead)
    from app.models.core import TeamMember

    db.query(TeamMember).filter(TeamMember.user_id == lead.id).update({"role": "member"})
    db.commit()
    assert run_due_templates() == {"not_allowed": 1}
    assert fake_smtp == []
    with SessionLocal() as session:
        row = session.get(ReportTemplate, template_id)
        assert row.last_status == "not_allowed" and row.next_run_at is not None


def test_scheduled_task_ignores_disabled(client, admin):
    template = create(client, admin, schedule={**SCHEDULE, "enabled": False}).json()
    with SessionLocal() as session:
        session.get(ReportTemplate, template["id"]).next_run_at = utcnow() - timedelta(minutes=5)
        session.commit()
    assert run_due_templates() == {}
    with SessionLocal() as session:
        assert session.get(ReportTemplate, template["id"]).next_run_at is None


def test_beat_schedule_registered():
    from app.tasks import celery_app

    entry = celery_app.conf.beat_schedule["reports-run-scheduled"]
    assert entry["task"] == "reports.run_scheduled" and entry["schedule"] == 900.0


# --------------------------------------------------------------------------- migración


def _alembic_config(url: str) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.attributes["sqlalchemy.url"] = url
    config.attributes["configure_logger"] = False
    return config


def test_migration_chain_and_model(tmp_path):
    url = f"sqlite:///{(tmp_path / 'reports.db').as_posix()}"
    config = _alembic_config(url)
    script = ScriptDirectory.from_config(config)
    revision = script.get_revision("0007_report_templates")
    assert revision is not None and revision.down_revision == "0006_catalog"
    assert script.get_heads() == ["0008_phase3_fixes"]

    command.upgrade(config, "head")
    engine = sa.create_engine(url)
    try:
        inspector = sa.inspect(engine)
        model_table = Base.metadata.tables["report_templates"]
        db_columns = {c["name"]: c for c in inspector.get_columns("report_templates")}
        assert set(db_columns) == set(model_table.columns.keys())
        for column in model_table.columns:
            assert db_columns[column.name]["nullable"] == column.nullable, column.name
        assert {ix.name for ix in model_table.indexes} <= {ix["name"] for ix in inspector.get_indexes("report_templates")}
    finally:
        engine.dispose()

    command.downgrade(config, "0006_catalog")
    engine = sa.create_engine(url)
    try:
        assert "report_templates" not in sa.inspect(engine).get_table_names()
    finally:
        engine.dispose()
