"""Adjuntos: subida, validación (tamaño, tipo, firma), permisos, descarga y borrado lógico."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.main import app
from app.models.attachments import TicketAttachment
from app.models.core import Ticket, TicketHistory, WorkflowState
from app.services.storage import InMemoryStorage, get_storage
from tests.conftest import DEPARTMENT_SOC

PDF = b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
EXE = b"MZ\x90\x00" + b"\x00" * 64


@pytest.fixture
def storage() -> Iterator[InMemoryStorage]:
    memory = InMemoryStorage()
    app.dependency_overrides[get_storage] = lambda: memory
    yield memory
    app.dependency_overrides.pop(get_storage, None)


@pytest.fixture
def upload(client, storage):
    def factory(
        persona: Any,
        ticket_id: int,
        filename: str = "evidencia.pdf",
        content: bytes = PDF,
        content_type: str = "application/pdf",
    ):
        return client.post(
            f"/tickets/{ticket_id}/attachments",
            files={"file": (filename, content, content_type)},
            headers=persona.headers,
        )

    return factory


@pytest.fixture
def ticket(create_ticket, requester) -> dict[str, Any]:
    """Ticket de Telecomunicaciones creado por el solicitante."""
    return create_ticket(requester)


# --------------------------------------------------------------------------- subida


def test_requester_uploads_to_own_ticket(upload, ticket, requester, storage, published_events, db):
    response = upload(requester, ticket["id"], filename="Informe enlace.pdf")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["filename"] == "Informe enlace.pdf"
    assert body["size"] == len(PDF)
    assert body["mime"] == "application/pdf"
    assert body["checksum"] == hashlib.sha256(PDF).hexdigest()
    assert body["uploaded_by"]["id"] == requester.id
    assert body["can_delete"] is True

    row = db.get(TicketAttachment, body["id"])
    assert row is not None and row.deleted_at is None
    # La clave del objeto es aleatoria y no contiene el nombre del usuario.
    assert row.object_key.startswith(f"tickets/{ticket['id']}/")
    assert "Informe" not in row.object_key
    assert storage.objects[row.object_key] == PDF
    assert storage.content_types[row.object_key] == "application/pdf"
    assert storage.metadata[row.object_key]["sha256"] == body["checksum"]

    added = [e for e in published_events if e["type"] == "attachment.added"]
    assert len(added) == 1
    assert added[0]["ticket_id"] == ticket["id"]
    assert added[0]["data"]["attachment_id"] == body["id"]
    assert added[0]["requester_id"] == requester.id
    history = db.scalars(
        select(TicketHistory).where(
            TicketHistory.ticket_id == ticket["id"], TicketHistory.event_type == "attachment.added"
        )
    ).all()
    assert len(history) == 1


def test_area_agent_can_upload_and_list(upload, ticket, agent, requester, client):
    assert upload(agent, ticket["id"], "captura.png", PNG, "image/png").status_code == 201
    assert upload(requester, ticket["id"]).status_code == 201

    response = client.get(f"/tickets/{ticket['id']}/attachments", headers=requester.headers)
    assert response.status_code == 200
    items = response.json()
    assert [i["filename"] for i in items] == ["captura.png", "evidencia.pdf"]
    # El solicitante solo puede borrar lo suyo; el agente del área puede borrar todo.
    assert [i["can_delete"] for i in items] == [False, True]
    as_agent = client.get(f"/tickets/{ticket['id']}/attachments", headers=agent.headers).json()
    assert all(i["can_delete"] for i in as_agent)


def test_other_users_cannot_access(upload, ticket, other_requester, other_agent, client, requester):
    created = upload(requester, ticket["id"]).json()
    for persona in (other_requester, other_agent):
        assert upload(persona, ticket["id"]).status_code == 404
        assert client.get(f"/tickets/{ticket['id']}/attachments", headers=persona.headers).status_code == 404
        assert client.get(f"/attachments/{created['id']}/download", headers=persona.headers).status_code == 404
        assert client.delete(f"/attachments/{created['id']}", headers=persona.headers).status_code == 404


def test_requester_cannot_upload_to_others_ticket_even_if_agent_visible(
    upload, create_ticket, other_agent, agent
):
    soc_ticket = create_ticket(other_agent, department=DEPARTMENT_SOC)
    # El agente de Telecom no ve tickets de SOC.
    assert upload(agent, soc_ticket["id"]).status_code == 404
    assert upload(other_agent, soc_ticket["id"]).status_code == 201


def test_unauthenticated_upload_rejected(client, ticket, storage):
    response = client.post(
        f"/tickets/{ticket['id']}/attachments", files={"file": ("a.pdf", PDF, "application/pdf")}
    )
    assert response.status_code == 401
    assert storage.objects == {}


# --------------------------------------------------------------------------- validación


@pytest.mark.parametrize(
    "filename,content,content_type",
    [
        ("malware.exe", EXE, "application/octet-stream"),
        ("pagina.html", b"<html><script>alert(1)</script></html>", "text/html"),
        ("logo.svg", b"<svg xmlns='http://www.w3.org/2000/svg'></svg>", "image/svg+xml"),
        ("sin_extension", PDF, "application/pdf"),
        ("script.js", b"alert(1)", "text/javascript"),
    ],
)
def test_disallowed_extensions(upload, ticket, requester, storage, filename, content, content_type):
    response = upload(requester, ticket["id"], filename, content, content_type)
    assert response.status_code == 415, response.text
    assert "no permitido" in response.json()["detail"]
    assert storage.objects == {}


@pytest.mark.parametrize(
    "filename,content,content_type",
    [
        ("factura.pdf", EXE, "application/pdf"),  # ejecutable renombrado
        ("foto.png", PDF, "image/png"),  # firma de otro tipo
        ("hoja.xlsx", b"no es un zip", "application/octet-stream"),
        ("notas.txt", b"\x00\x01\x02\x03binario\x00\x00", "text/plain"),
    ],
)
def test_magic_signature_mismatch(upload, ticket, requester, storage, filename, content, content_type):
    response = upload(requester, ticket["id"], filename, content, content_type)
    assert response.status_code == 415, response.text
    assert "contenido" in response.json()["detail"]
    assert storage.objects == {}


def test_declared_mime_must_match_extension(upload, ticket, requester):
    response = upload(requester, ticket["id"], "doc.pdf", PDF, "text/html")
    assert response.status_code == 415
    assert "tipo declarado" in response.json()["detail"]


def test_generic_and_alias_mimes_are_accepted(upload, ticket, requester, db):
    assert upload(requester, ticket["id"], "doc.pdf", PDF, "application/octet-stream").status_code == 201
    # Windows envía CSV como application/vnd.ms-excel; se almacena el MIME canónico.
    response = upload(requester, ticket["id"], "datos.csv", b"a,b\n1,2\n", "application/vnd.ms-excel")
    assert response.status_code == 201, response.text
    assert response.json()["mime"] == "text/csv"


def test_empty_file_rejected(upload, ticket, requester):
    response = upload(requester, ticket["id"], "vacio.pdf", b"", "application/pdf")
    assert response.status_code == 422


def test_size_limit_enforced_on_body(upload, ticket, requester, storage, monkeypatch):
    monkeypatch.setattr(settings, "max_attachment_mb", 1)
    limit = 1024 * 1024
    exact = PDF + b"0" * (limit - len(PDF))
    assert upload(requester, ticket["id"], "justo.pdf", exact).status_code == 201

    over = PDF + b"0" * (limit - len(PDF) + 1)
    response = upload(requester, ticket["id"], "grande.pdf", over)
    assert response.status_code == 413
    assert "1 MB" in response.json()["detail"]
    assert len(storage.objects) == 1


def test_size_limit_enforced_by_content_length(upload, ticket, requester, storage, monkeypatch):
    monkeypatch.setattr(settings, "max_attachment_mb", 1)
    huge = PDF + b"0" * (2 * 1024 * 1024)
    response = upload(requester, ticket["id"], "enorme.pdf", huge)
    assert response.status_code == 413
    assert storage.objects == {}


def test_missing_file_field_and_wrong_content_type(client, ticket, requester, storage):
    response = client.post(
        f"/tickets/{ticket['id']}/attachments",
        files={"otro": ("a.pdf", PDF, "application/pdf")},
        headers=requester.headers,
    )
    assert response.status_code == 422
    response = client.post(
        f"/tickets/{ticket['id']}/attachments", json={"file": "x"}, headers=requester.headers
    )
    assert response.status_code == 415


def test_filename_is_sanitized(upload, ticket, requester):
    response = upload(requester, ticket["id"], "..\\..\\etc/pass<wd>‮gnp.pdf", PDF)
    assert response.status_code == 201, response.text
    assert response.json()["filename"] == "pass_wd_gnp.pdf"


def test_closed_ticket_rejects_uploads(upload, ticket, requester, db):
    closed = db.scalar(select(WorkflowState).where(WorkflowState.key == "closed"))
    db.get(Ticket, ticket["id"]).status_id = closed.id
    db.commit()
    response = upload(requester, ticket["id"])
    assert response.status_code == 409


def test_storage_failure_returns_503_without_row(upload, ticket, requester, storage, db):
    storage.fail_puts = True
    response = upload(requester, ticket["id"])
    assert response.status_code == 503
    assert db.scalars(select(TicketAttachment)).all() == []


def test_per_ticket_limit(upload, ticket, requester, monkeypatch):
    from app.routers import attachments as module

    monkeypatch.setattr(module, "MAX_ATTACHMENTS_PER_TICKET", 2)
    assert upload(requester, ticket["id"]).status_code == 201
    assert upload(requester, ticket["id"]).status_code == 201
    response = upload(requester, ticket["id"])
    assert response.status_code == 409


# --------------------------------------------------------------------------- descarga


def test_download_streams_with_safe_headers(upload, ticket, requester, agent, client):
    created = upload(requester, ticket["id"], "Diagnóstico red.pdf").json()
    response = client.get(f"/attachments/{created['id']}/download", headers=agent.headers)
    assert response.status_code == 200
    assert response.content == PDF
    assert response.headers["content-type"] == "application/pdf"
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    assert "filename*=UTF-8''Diagn%C3%B3stico%20red.pdf" in disposition
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in response.headers["content-security-policy"]
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["content-length"] == str(len(PDF))


def test_download_missing_object_returns_404(upload, ticket, requester, client, storage):
    created = upload(requester, ticket["id"]).json()
    storage.objects.clear()
    response = client.get(f"/attachments/{created['id']}/download", headers=requester.headers)
    assert response.status_code == 404
    assert "almacenamiento" in response.json()["detail"]


# --------------------------------------------------------------------------- borrado


def test_delete_permissions_and_soft_delete(
    upload, ticket, requester, agent, other_agent, client, storage, published_events, db
):
    by_agent = upload(agent, ticket["id"], "captura.png", PNG, "image/png").json()
    by_requester = upload(requester, ticket["id"]).json()

    # El solicitante no puede borrar el adjunto del agente.
    response = client.delete(f"/attachments/{by_agent['id']}", headers=requester.headers)
    assert response.status_code == 403
    # Pero sí el suyo.
    assert client.delete(f"/attachments/{by_requester['id']}", headers=requester.headers).status_code == 204
    # Un agente del área puede borrar cualquiera.
    assert client.delete(f"/attachments/{by_agent['id']}", headers=agent.headers).status_code == 204

    listed = client.get(f"/tickets/{ticket['id']}/attachments", headers=agent.headers).json()
    assert listed == []
    assert client.get(f"/attachments/{by_agent['id']}/download", headers=agent.headers).status_code == 404
    assert client.delete(f"/attachments/{by_agent['id']}", headers=agent.headers).status_code == 404

    # Borrado lógico: la fila y el objeto se conservan para auditoría.
    db.expire_all()
    row = db.get(TicketAttachment, by_agent["id"])
    assert row is not None and row.deleted_at is not None
    assert row.object_key in storage.objects
    deleted = [e for e in published_events if e["type"] == "attachment.deleted"]
    assert {e["data"]["attachment_id"] for e in deleted} == {by_agent["id"], by_requester["id"]}


def test_admin_can_delete_any(upload, ticket, requester, admin, client):
    created = upload(requester, ticket["id"]).json()
    assert client.delete(f"/attachments/{created['id']}", headers=admin.headers).status_code == 204


def test_settings_endpoint(client, requester, storage):
    response = client.get("/attachments/settings", headers=requester.headers)
    assert response.status_code == 200
    body = response.json()
    assert body["max_mb"] == settings.max_attachment_mb
    assert "pdf" in body["allowed_extensions"]
    assert "exe" not in body["allowed_extensions"] and "svg" not in body["allowed_extensions"]


def test_per_user_daily_quota(client, upload, create_ticket, requester, admin, monkeypatch):
    """Cuota anti-abuso: nº de adjuntos y bytes por usuario en 24 h (el admin queda exento)."""
    monkeypatch.setattr(settings, "attachment_user_daily_count", 2)
    monkeypatch.setattr(settings, "attachment_user_daily_mb", 1)
    first = create_ticket(requester)
    second = create_ticket(requester, title="Otro ticket del mismo usuario")
    assert upload(requester, first["id"]).status_code == 201
    deleted = upload(requester, second["id"])
    assert deleted.status_code == 201
    # Borrar no libera la cuota (el archivo sigue en el almacenamiento hasta la purga).
    removed = client.delete(f"/attachments/{deleted.json()['id']}", headers=requester.headers)
    assert removed.status_code == 204
    blocked = upload(requester, second["id"])
    assert blocked.status_code == 429, blocked.text
    assert "24 horas" in blocked.json()["detail"]
    assert upload(admin, second["id"]).status_code == 201


def test_per_user_daily_bytes_quota(upload, ticket, requester, monkeypatch):
    monkeypatch.setattr(settings, "attachment_user_daily_count", 0)
    monkeypatch.setattr(settings, "attachment_user_daily_mb", 1)
    big = PDF + b"0" * (700 * 1024)
    assert upload(requester, ticket["id"], content=big).status_code == 201
    over = upload(requester, ticket["id"], content=big)
    assert over.status_code == 429
    assert "MB" in over.json()["detail"]
