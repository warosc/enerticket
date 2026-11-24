"""Catálogo de servicio — administración, GET /catalog, importación CSV y migración 0006."""

from __future__ import annotations

import importlib

import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy import select

from app.core.db import SessionLocal
from app.models.catalog import TicketCategory, TicketSubcategory
from app.models.sla import SlaPolicy
from tests.conftest import DEPARTMENT_SOC, DEPARTMENT_TELECOM
from tests.test_core_migrations import alembic_config

ADMIN_ENDPOINTS = [
    ("get", "/admin/gerencias", None),
    ("post", "/admin/gerencias", {"name": "Gerencia X"}),
    ("get", "/admin/department-aliases", None),
    ("post", "/admin/department-aliases", {"value": "X"}),
    ("get", "/admin/categories", None),
    ("post", "/admin/categories", {"department_id": 1, "name": "X"}),
    ("get", "/admin/providers", None),
    ("post", "/admin/providers", {"name": "Proveedor X"}),
    ("patch", "/admin/users/1/profile", {"vip": True}),
    ("get", "/admin/gerencias/1/viewers", None),
]


@pytest.mark.parametrize("method, url, body", ADMIN_ENDPOINTS)
def test_admin_endpoints_require_admin(client, agent, requester, method, url, body):
    for persona in (agent, requester):
        kwargs = {"json": body} if body is not None else {}
        response = getattr(client, method)(url, headers=persona.headers, **kwargs)
        assert response.status_code == 403, (url, persona.username)
    files = {"file": ("catalogo.csv", b"departamento,categoria\n", "text/csv")}
    assert client.post("/admin/catalog/import", files=files, headers=agent.headers).status_code == 403


def test_gerencias_and_departments(client, admin):
    created = client.post(
        "/admin/gerencias", json={"name": "Gerencia Comercial", "external_group": "GG-Ger-Comercial"},
        headers=admin.headers,
    )
    assert created.status_code == 201, created.text
    gerencia = created.json()
    assert client.post("/admin/gerencias", json={"name": "Gerencia Comercial"}, headers=admin.headers).status_code == 409
    department = client.post(
        "/admin/departments", json={"name": "Comercial", "gerencia_id": gerencia["id"]}, headers=admin.headers
    ).json()
    assert department["gerencia_id"] == gerencia["id"]
    assert department["serves_tickets"] is False and department["active"] is True
    patched = client.patch(
        f"/admin/departments/{department['id']}", json={"serves_tickets": True, "active": False},
        headers=admin.headers,
    ).json()
    assert patched["serves_tickets"] is True and patched["active"] is False
    bad = client.patch(f"/admin/departments/{department['id']}", json={"gerencia_id": 9999}, headers=admin.headers)
    assert bad.status_code == 422
    listing = client.get("/admin/gerencias", headers=admin.headers).json()
    assert listing[0]["department_ids"] == [department["id"]]
    renamed = client.patch(
        f"/admin/gerencias/{gerencia['id']}", json={"name": "Gerencia de Ventas", "external_group": ""},
        headers=admin.headers,
    ).json()
    assert renamed["name"] == "Gerencia de Ventas" and renamed["external_group"] is None


def test_department_aliases_crud(client, admin, ids):
    telecom = ids["departments"][DEPARTMENT_TELECOM]
    created = client.post(
        "/admin/department-aliases", json={"value": "07 Telecom", "department_id": telecom}, headers=admin.headers
    )
    assert created.status_code == 201
    alias = created.json()
    assert alias["normalized_value"] == "TELECOM" and alias["pending"] is False
    duplicate = client.post("/admin/department-aliases", json={"value": "telecom"}, headers=admin.headers)
    assert duplicate.status_code == 409
    assert client.post("/admin/department-aliases", json={"value": " 	 "}, headers=admin.headers).status_code == 422
    client.post("/admin/department-aliases", json={"value": "Bodega"}, headers=admin.headers)
    pending = client.get("/admin/department-aliases?pending=true", headers=admin.headers).json()
    assert [a["normalized_value"] for a in pending] == ["BODEGA"]
    assigned = client.get("/admin/department-aliases?pending=false", headers=admin.headers).json()
    assert [a["normalized_value"] for a in assigned] == ["TELECOM"]
    unassigned = client.patch(
        f"/admin/department-aliases/{alias['id']}", json={"department_id": None}, headers=admin.headers
    ).json()
    assert unassigned["pending"] is True
    assert client.delete(f"/admin/department-aliases/{alias['id']}", headers=admin.headers).status_code == 204
    assert len(client.get("/admin/department-aliases", headers=admin.headers).json()) == 1


def test_categories_subcategories_and_providers(client, admin, requester, ids):
    telecom = ids["departments"][DEPARTMENT_TELECOM]
    response = client.post(
        "/admin/categories",
        json={"department_id": telecom, "name": "Telefonía Móvil", "type_keys": ["incident", "service_request"]},
        headers=admin.headers,
    )
    assert response.status_code == 201, response.text
    category = response.json()
    assert category["department"] == {"id": telecom, "name": DEPARTMENT_TELECOM}
    assert category["subcategories"] == [] and category["active"] is True
    assert client.post(
        "/admin/categories", json={"department_id": telecom, "name": "Telefonía Móvil"}, headers=admin.headers
    ).status_code == 409
    assert client.post(
        "/admin/categories", json={"department_id": telecom, "name": "Otra", "type_keys": ["nope"]},
        headers=admin.headers,
    ).status_code == 422
    with_sub = client.post(
        f"/admin/categories/{category['id']}/subcategories", json={"name": "Equipo dañado"}, headers=admin.headers
    )
    assert with_sub.status_code == 201
    sub = with_sub.json()["subcategories"][0]
    assert sub["name"] == "Equipo dañado" and sub["active"] is True
    assert client.post(
        f"/admin/categories/{category['id']}/subcategories", json={"name": "Equipo dañado"}, headers=admin.headers
    ).status_code == 409
    client.post(f"/admin/categories/{category['id']}/subcategories", json={"name": "Línea"}, headers=admin.headers)
    client.patch(f"/admin/subcategories/{sub['id']}", json={"active": False}, headers=admin.headers)
    inactive = client.post(
        "/admin/categories", json={"department_id": telecom, "name": "Retirada"}, headers=admin.headers
    ).json()
    client.patch(f"/admin/categories/{inactive['id']}", json={"active": False}, headers=admin.headers)
    listing = client.get(f"/admin/categories?department_id={telecom}", headers=admin.headers).json()
    assert {c["name"] for c in listing} == {"Telefonía Móvil", "Retirada"}

    provider = client.post(
        "/admin/providers", json={"name": "UFINET", "department_ids": [telecom]}, headers=admin.headers
    )
    assert provider.status_code == 201
    assert client.post("/admin/providers", json={"name": "UFINET"}, headers=admin.headers).status_code == 409
    assert client.post(
        "/admin/providers", json={"name": "Otro", "department_ids": [9999]}, headers=admin.headers
    ).status_code == 422
    retired = client.post("/admin/providers", json={"name": "Retirado"}, headers=admin.headers).json()
    client.patch(f"/admin/providers/{retired['id']}", json={"active": False}, headers=admin.headers)

    catalog = client.get("/catalog", headers=requester.headers).json()
    assert [(c["name"], [s["name"] for s in c["subcategories"]]) for c in catalog["categories"]] == [
        ("Telefonía Móvil", ["Línea"])
    ]
    assert catalog["categories"][0]["type_keys"] == ["incident", "service_request"]
    assert [p["name"] for p in catalog["providers"]] == ["UFINET"]
    assert [c["key"] for c in catalog["channels"]] == ["portal", "email", "phone", "other"]
    assert catalog["channels"][2]["label"] == "Teléfono"
    telecom_out = next(d for d in catalog["departments"] if d["id"] == telecom)
    assert telecom_out["serves_tickets"] is True and telecom_out["gerencia_id"] is None
    names = {d["name"] for d in catalog["departments"] if d["serves_tickets"]}
    assert {"Sistemas Comerciales", "Sistemas Corporativos", "Seguridad Informática", "Soporte Técnico"} <= names
    types = {t["key"]: t["name"] for t in catalog["ticket_types"]}
    assert types == {"incident": "Incidencia", "service_request": "Petición", "work_order": "Orden de trabajo"}


# --------------------------------------------------------------------------- importación CSV


@pytest.fixture
def sla_seed():
    seed = importlib.import_module("app.seeds.10_sla")
    with SessionLocal() as session:
        seed.run(session)
        session.commit()


def _import(client, admin, content: str | bytes, dry_run: bool):
    data = content.encode("utf-8") if isinstance(content, str) else content
    files = {"file": ("catalogo.csv", data, "text/csv")}
    return client.post(
        f"/admin/catalog/import?dry_run={'true' if dry_run else 'false'}", files=files, headers=admin.headers
    )


CSV = (
    "departamento,categoria,subcategoria,tipos,ans_horas\n"
    "Telecomunicaciones,Telefonía Móvil,Equipo dañado,incident|service_request,8\n"
    "telecomunicaciones,TELEFONÍA MÓVIL,Línea nueva,,\n"
    "Telecomunicaciones,Enlaces,,incident,4\n"
    "Ciberseguridad / SOC,Malware,,,\n"
)


def _catalog_counts():
    with SessionLocal() as session:
        return (
            len(session.scalars(select(TicketCategory)).all()),
            len(session.scalars(select(TicketSubcategory)).all()),
            len(session.scalars(select(SlaPolicy).where(SlaPolicy.name.like("ANS %"))).all()),
        )


def test_import_dry_run_then_apply_is_idempotent(client, admin, sla_seed, ids):
    preview = _import(client, admin, CSV, dry_run=True)
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body == {
        "dry_run": True,
        "created": {"categories": 3, "subcategories": 2, "policies": 2},
        "updated": {"policies": 0},
        "errors": [],
    }
    assert _catalog_counts() == (0, 0, 0)

    applied = _import(client, admin, CSV, dry_run=False).json()
    assert applied["dry_run"] is False and applied["created"] == body["created"]
    assert _catalog_counts() == (3, 2, 2)
    with SessionLocal() as session:
        policy = session.scalar(
            select(SlaPolicy).where(SlaPolicy.name == "ANS Telecomunicaciones / Telefonía Móvil / Equipo dañado")
        )
        assert policy.resolution_minutes == 480 and policy.precedence == 50
        assert policy.subcategory_ids and policy.category_ids == []
        assert policy.department_ids == [ids["departments"][DEPARTMENT_TELECOM]]
        links = session.scalar(select(SlaPolicy).where(SlaPolicy.name == "ANS Telecomunicaciones / Enlaces"))
        assert links.category_ids and links.resolution_minutes == 240
        mobile = session.scalar(select(TicketCategory).where(TicketCategory.name == "Telefonía Móvil"))
        assert mobile.type_keys == ["incident", "service_request"]

    again = _import(client, admin, CSV, dry_run=False).json()
    assert again["created"] == {"categories": 0, "subcategories": 0, "policies": 0}
    assert again["updated"] == {"policies": 0}
    assert _catalog_counts() == (3, 2, 2)

    # Cambiar las horas actualiza la política; una categoría desactivada se reactiva.
    with SessionLocal() as session:
        session.scalar(select(TicketCategory).where(TicketCategory.name == "Malware")).active = False
        session.commit()
    changed = _import(client, admin, CSV.replace(",incident,4", ",incident,6"), dry_run=False).json()
    assert changed["updated"] == {"policies": 1} and changed["created"]["policies"] == 0
    with SessionLocal() as session:
        assert session.scalar(select(TicketCategory).where(TicketCategory.name == "Malware")).active is True


def test_import_reports_errors_per_line(client, admin, sla_seed):
    content = (
        "Departamento;Categoría;Subcategoría;Tipos;ANS horas\n"
        "Telecomunicaciones;Enlaces;;incident;2,5\n"
        "Inexistente;Algo;;;\n"
        "Telecomunicaciones;;;;\n"
        "\n"
        "Telecomunicaciones;Voz;;change|cosa;\n"
        "Telecomunicaciones;Datos;;;-3\n"
        "Telecomunicaciones;Datos;;;muchas\n"
        f"Telecomunicaciones;{'x' * 151};;;\n"
    )
    body = _import(client, admin, content, dry_run=False).json()
    assert body["created"]["categories"] == 1 and body["created"]["policies"] == 1
    lines = {e["line"]: e["message"] for e in body["errors"]}
    assert sorted(lines) == [3, 4, 6, 7, 8, 9]
    assert "Departamento desconocido" in lines[3]
    assert "Falta la categoría" in lines[4]
    assert "Tipos desconocidos" in lines[6] and "change" in lines[6]
    assert "ans_horas" in lines[7] and "ans_horas" in lines[8]
    assert "150" in lines[9]
    with SessionLocal() as session:
        policy = session.scalar(select(SlaPolicy).where(SlaPolicy.name == "ANS Telecomunicaciones / Enlaces"))
        assert policy.resolution_minutes == 150


def test_import_sanitizes_formula_injection(client, admin):
    content = (
        "departamento,categoria,subcategoria\n"
        'Telecomunicaciones,"=HYPERLINK(""http://x"",""clic"")",@SUM(A1)\n'
        "Telecomunicaciones,+cmd|' /C calc'!A0,-\tTab\x07\n"
    )
    body = _import(client, admin, content, dry_run=False).json()
    assert body["errors"] == []
    with SessionLocal() as session:
        names = sorted(c.name for c in session.scalars(select(TicketCategory)).all())
        subs = sorted(s.name for s in session.scalars(select(TicketSubcategory)).all())
    assert names == ['HYPERLINK("http://x","clic")', "cmd|' /C calc'!A0"]
    assert subs == ["SUM(A1)", "Tab"]
    assert all(not n.startswith(("=", "+", "-", "@")) for n in names + subs)


def test_import_rejects_bad_files(client, admin):
    assert _import(client, admin, "categoria,tipos\nX,\n", dry_run=True).status_code == 422
    assert _import(client, admin, "", dry_run=True).status_code == 422
    assert _import(client, admin, b"departamento,categoria\n\xff\xfe,x\n", dry_run=True).status_code == 422
    big = "departamento,categoria\n" + ("Telecomunicaciones,X\n" * 120000)
    assert _import(client, admin, big, dry_run=True).status_code == 413
    many = "departamento,categoria\n" + "".join(f"Telecomunicaciones,C{i}\n" for i in range(5001))
    response = _import(client, admin, many, dry_run=True)
    assert response.status_code == 422 and "5000" in response.json()["detail"]


def test_import_hours_without_default_calendar_is_a_line_error(client, admin):
    body = _import(client, admin, "departamento,categoria,ans_horas\nTelecomunicaciones,Enlaces,4\n", False).json()
    assert body["errors"][0]["line"] == 2 and "calendario" in body["errors"][0]["message"]
    assert body["created"]["categories"] == 0


def test_imported_catalog_drives_ticket_rules(client, admin, agent, requester, sla_seed, ids, create_ticket, transition):
    _import(client, admin, CSV, dry_run=False)
    # Las ANS importadas tienen precedencia 50 (contrato): las políticas por prioridad sembradas
    # (10-40) se evalúan antes, así que el administrador las desactiva para usar las del catálogo.
    with SessionLocal() as session:
        for policy in session.scalars(select(SlaPolicy).where(SlaPolicy.name.not_like("ANS %"))).all():
            policy.active = False
        session.commit()
    catalog = client.get("/catalog", headers=requester.headers).json()
    links = next(c for c in catalog["categories"] if c["name"] == "Enlaces")
    ticket = create_ticket(requester, category_id=links["id"])
    sla = client.get(f"/tickets/{ticket['id']}/sla", headers=agent.headers).json()
    assert sla["resolution"]["target_minutes"] == 240
    malware = next(c for c in catalog["categories"] if c["name"] == "Malware")
    soc_ticket = create_ticket(requester, department=DEPARTMENT_SOC, type_key="work_order", category_id=malware["id"])
    assert soc_ticket["type"]["key"] == "work_order"
    response = client.post(
        "/tickets",
        json={"title": "Petición", "department_id": ids["departments"][DEPARTMENT_TELECOM],
              "type_key": "service_request", "category_id": links["id"]},
        headers=requester.headers,
    )
    assert response.status_code == 422


# --------------------------------------------------------------------------- migración


@pytest.fixture
def sqlite_url(tmp_path) -> str:
    return f"sqlite:///{(tmp_path / 'catalog_migration.db').as_posix()}"


def test_migration_0006_marks_existing_departments_and_downgrades(sqlite_url):
    config = alembic_config(sqlite_url)
    command.upgrade(config, "0005_seed_state")
    engine = sa.create_engine(sqlite_url)
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql("INSERT INTO departments (id, name) VALUES (1, 'Telecomunicaciones')")
    finally:
        engine.dispose()
    command.upgrade(config, "0006_catalog")
    engine = sa.create_engine(sqlite_url)
    try:
        inspector = sa.inspect(engine)
        tables = set(inspector.get_table_names())
        assert {"gerencias", "department_aliases", "ticket_categories", "ticket_subcategories",
                "providers", "gerencia_viewers"} <= tables
        with engine.connect() as conn:
            row = conn.exec_driver_sql("SELECT serves_tickets, active FROM departments").one()
        assert bool(row[0]) is True and bool(row[1]) is True
        ticket_columns = {c["name"] for c in inspector.get_columns("tickets")}
        assert {"category_id", "channel", "vip", "business_resolution_minutes", "parent_ticket_id"} <= ticket_columns
    finally:
        engine.dispose()
    command.downgrade(config, "0005_seed_state")
    engine = sa.create_engine(sqlite_url)
    try:
        inspector = sa.inspect(engine)
        assert "ticket_categories" not in inspector.get_table_names()
        assert "category_id" not in {c["name"] for c in inspector.get_columns("tickets")}
        assert "serves_tickets" not in {c["name"] for c in inspector.get_columns("departments")}
        with engine.connect() as conn:
            assert conn.exec_driver_sql("SELECT name FROM departments").scalar() == "Telecomunicaciones"
    finally:
        engine.dispose()


def test_types_and_departments_v2_seed_once():
    seed = importlib.import_module("app.seeds.00_core")
    from app.models.core import Department, TicketType

    with SessionLocal() as session:
        session.scalar(select(TicketType).where(TicketType.key == "change")).active = True
        session.delete(session.scalar(select(Department).where(Department.name == "Soporte Técnico")).teams[0])
        session.delete(session.scalar(select(Department).where(Department.name == "Soporte Técnico")))
        session.commit()
    with SessionLocal() as session:
        seed.run(session)
        session.commit()
        # Marcas de una sola vez: no se vuelve a desactivar ni a crear lo que el admin cambió.
        assert session.scalar(select(TicketType).where(TicketType.key == "change")).active is True
        assert session.scalar(select(Department).where(Department.name == "Soporte Técnico")) is None
        assert session.scalar(select(TicketType).where(TicketType.key == "problem")).active is False
