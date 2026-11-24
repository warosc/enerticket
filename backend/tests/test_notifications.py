"""Notificaciones: bandeja in-app, eventos que notifican, notification.created y correo (SMTP simulado)."""

from __future__ import annotations

import smtplib
from typing import Any

import pytest
from sqlalchemy import select

from app.core import events
from app.core.config import settings
from app.core.db import SessionLocal
from app.models.automation import Notification
from app.models.core import Ticket
from app.services import notifications as notif_svc
from app.services.notifications import email as email_svc
from tests.conftest import DEPARTMENT_TELECOM


class FakeSMTP:
    """Cliente SMTP simulado: registra los mensajes o falla según ``fail_with``."""

    sent: list[Any] = []
    logins: list[tuple[str, str]] = []
    fail_with: Exception | None = None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    def login(self, user: str, password: str) -> None:
        FakeSMTP.logins.append((user, password))

    def send_message(self, message: Any) -> None:
        if FakeSMTP.fail_with is not None:
            raise FakeSMTP.fail_with
        FakeSMTP.sent.append(message)

    def quit(self) -> None:
        pass


@pytest.fixture(autouse=True)
def fake_mail(monkeypatch):
    """Sin Celery ni SMTP reales: captura los ids despachados y usa FakeSMTP."""
    dispatched: list[int] = []
    FakeSMTP.sent = []
    FakeSMTP.logins = []
    FakeSMTP.fail_with = None
    notif_svc.set_email_dispatcher(lambda ids: dispatched.extend(ids))
    email_svc.set_smtp_factory(FakeSMTP)
    yield dispatched
    notif_svc.set_email_dispatcher(None)
    email_svc.set_smtp_factory(None)


@pytest.fixture
def smtp_on(monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.test")
    monkeypatch.setattr(settings, "smtp_user", "mailer")
    monkeypatch.setattr(settings, "smtp_password", "replace-me")


def _inbox(user_id: int) -> list[Notification]:
    with SessionLocal() as session:
        return list(
            session.scalars(
                select(Notification)
                .where(Notification.user_id == user_id, Notification.channel == "in_app")
                .order_by(Notification.id)
            ).all()
        )


# --------------------------------------------------------------------------- disparadores


def test_assignment_notifies_assignee_and_api_inbox(client, admin, agent, requester, create_ticket):
    ticket = create_ticket(requester)
    client.patch(f"/tickets/{ticket['id']}", json={"assignee_id": agent.id}, headers=admin.headers)

    count = client.get("/notifications/unread-count", headers=agent.headers).json()
    assert count == {"count": 1}
    page = client.get("/notifications", headers=agent.headers).json()
    assert page["total"] == 1 and page["unread"] == 1
    item = page["items"][0]
    assert item["kind"] == "ticket_assigned"
    assert item["ticket"] == {"id": ticket["id"], "key": ticket["key"]}
    assert ticket["key"] in item["title"] and item["read"] is False

    # Asignarse a sí mismo no notifica.
    client.patch(f"/tickets/{ticket['id']}", json={"assignee_id": None}, headers=agent.headers)
    client.patch(f"/tickets/{ticket['id']}", json={"assignee_id": agent.id}, headers=agent.headers)
    assert client.get("/notifications/unread-count", headers=agent.headers).json() == {"count": 1}


def test_mark_read_and_read_all(client, admin, agent, requester, create_ticket):
    for _ in range(3):
        ticket = create_ticket(requester)
        client.patch(f"/tickets/{ticket['id']}", json={"assignee_id": agent.id}, headers=admin.headers)
    items = client.get("/notifications", headers=agent.headers).json()["items"]
    assert len(items) == 3

    read = client.post(f"/notifications/{items[0]['id']}/read", headers=agent.headers)
    assert read.status_code == 200 and read.json()["read"] is True and read.json()["read_at"]
    unread_only = client.get("/notifications", params={"unread": "true"}, headers=agent.headers).json()
    assert unread_only["total"] == 2 and unread_only["unread"] == 2

    # No se puede leer la notificación de otro usuario.
    assert client.post(f"/notifications/{items[1]['id']}/read", headers=requester.headers).status_code == 404

    all_read = client.post("/notifications/read-all", headers=agent.headers).json()
    assert all_read["updated"] == 2
    assert client.get("/notifications/unread-count", headers=agent.headers).json() == {"count": 0}


def test_public_comment_notifies_requester_but_internal_does_not(
    client, agent, requester, create_ticket
):
    ticket = create_ticket(requester)
    client.post(f"/tickets/{ticket['id']}/comments", json={"body": "Nota interna", "visibility": "internal"}, headers=agent.headers)
    assert _inbox(requester.id) == []
    client.post(f"/tickets/{ticket['id']}/comments", json={"body": "Estamos revisando su caso", "visibility": "public"}, headers=agent.headers)
    inbox = _inbox(requester.id)
    assert len(inbox) == 1 and inbox[0].kind == "ticket_commented"
    assert "Estamos revisando su caso" in inbox[0].body


def test_requester_comment_notifies_assignee(client, admin, agent, requester, create_ticket):
    ticket = create_ticket(requester)
    client.patch(f"/tickets/{ticket['id']}", json={"assignee_id": agent.id}, headers=admin.headers)
    client.post(f"/tickets/{ticket['id']}/comments", json={"body": "¿Novedades?", "visibility": "public"}, headers=requester.headers)
    kinds = [n.kind for n in _inbox(agent.id)]
    assert kinds == ["ticket_assigned", "ticket_commented"]
    assert _inbox(requester.id) == []


def test_resolution_notifies_requester(client, agent, requester, create_ticket, transition):
    ticket = create_ticket(requester)
    assert transition(agent, ticket["id"], "in_progress").status_code == 200
    assert transition(agent, ticket["id"], "resolved").status_code == 200
    resolved = [n for n in _inbox(requester.id) if n.kind == "ticket_resolved"]
    assert len(resolved) == 1 and "resuelto" in resolved[0].title
    # Cerrar un ticket ya resuelto no vuelve a notificar.
    assert transition(requester, ticket["id"], "closed").status_code == 200
    assert len([n for n in _inbox(requester.id) if n.kind == "ticket_resolved"]) == 1


def test_sla_events_notify_assignee_and_team_leads(client, admin, agent, make_user, requester, create_ticket):
    lead = make_user("jefe.telecom", roles=("agent",), departments=(DEPARTMENT_TELECOM,), team_role="lead")
    ticket = create_ticket(requester)
    client.patch(f"/tickets/{ticket['id']}", json={"assignee_id": agent.id}, headers=admin.headers)
    with SessionLocal() as session:
        db_ticket = session.get(Ticket, ticket["id"])
        events.emit(session, "sla.warning", db_ticket, None, {"metric": "resolution"})
        events.emit(session, "sla.breached", db_ticket, None, {"metric": "resolution"})
        session.commit()
    for user in (agent, lead):
        kinds = [n.kind for n in _inbox(user.id)]
        assert "sla_warning" in kinds and "sla_breached" in kinds
    breached = next(n for n in _inbox(lead.id) if n.kind == "sla_breached")
    assert breached.title == f"SLA de resolución incumplido: {ticket['key']}"
    assert _inbox(requester.id) == []


def test_notification_created_event_targets_recipient(client, admin, agent, requester, create_ticket, published_events):
    ticket = create_ticket(requester)
    published_events.clear()
    client.patch(f"/tickets/{ticket['id']}", json={"assignee_id": agent.id}, headers=admin.headers)
    created = [p for p in published_events if p["type"] == "notification.created"]
    assert len(created) == 1
    payload = created[0]
    assert payload["requester_id"] == agent.id
    assert payload["ticket_id"] == ticket["id"]
    assert payload["department_id"] is None and payload["team_id"] is None
    assert payload["data"]["user_id"] == agent.id
    assert payload["data"]["ticket_id"] == ticket["id"]
    assert isinstance(payload["data"]["notification_id"], int)


def test_inactive_and_system_users_are_not_notified(client, admin, agent, requester, create_ticket):
    with SessionLocal() as session:
        from app.models.core import User

        user = session.get(User, requester.id)
        notif_svc.notify(session, [requester.id, None, requester.id], kind="automation", title="Hola")
        session.commit()
        assert len(_inbox(requester.id)) == 1  # deduplicado
        user.active = False
        session.commit()
        assert notif_svc.notify(session, [requester.id], kind="automation", title="Otra") == []


# --------------------------------------------------------------------------- correo


def test_no_email_rows_when_smtp_disabled(client, admin, agent, requester, create_ticket, fake_mail):
    ticket = create_ticket(requester)
    client.patch(f"/tickets/{ticket['id']}", json={"assignee_id": agent.id}, headers=admin.headers)
    with SessionLocal() as session:
        assert session.scalars(select(Notification).where(Notification.channel == "email")).all() == []
    assert fake_mail == []


def test_email_is_queued_after_commit_and_delivered(smtp_on, client, admin, agent, requester, create_ticket, fake_mail):
    ticket = create_ticket(requester)
    client.patch(f"/tickets/{ticket['id']}", json={"assignee_id": agent.id}, headers=admin.headers)
    with SessionLocal() as session:
        mail = session.scalar(select(Notification).where(Notification.channel == "email"))
        assert mail is not None and mail.delivery_status == "pending" and mail.user_id == agent.id
        assert fake_mail == [mail.id]

        status = email_svc.deliver_email(session, mail)
        session.commit()
        assert status == "sent" and mail.sent_at is not None and mail.attempts == 1

    assert FakeSMTP.logins == [("mailer", "replace-me")]
    message = FakeSMTP.sent[0]
    assert message["To"].endswith("<ana.telecom@example.test>")
    assert message["Subject"] == f"[Service Desk] Se le asignó el ticket {ticket['key']}"
    html = message.get_body(preferencelist=("html",)).get_content()
    text = message.get_body(preferencelist=("plain",)).get_content()
    assert "Ver ticket" in html and f"/tickets/{ticket['id']}" in html
    assert "Hola Ana Telecom:" in text

    # Reenviar uno ya enviado no duplica.
    with SessionLocal() as session:
        mail = session.get(Notification, mail.id)
        assert email_svc.deliver_email(session, mail) == "sent"
    assert len(FakeSMTP.sent) == 1


def test_email_failures_retry_then_fail(smtp_on, client, admin, agent, requester, create_ticket):
    ticket = create_ticket(requester)
    client.patch(f"/tickets/{ticket['id']}", json={"assignee_id": agent.id}, headers=admin.headers)
    FakeSMTP.fail_with = smtplib.SMTPServerDisconnected("conexión cerrada")
    with SessionLocal() as session:
        mail = session.scalar(select(Notification).where(Notification.channel == "email"))
        for attempt in range(1, email_svc.MAX_ATTEMPTS):
            with pytest.raises(smtplib.SMTPServerDisconnected):
                email_svc.deliver_email(session, mail)
            assert mail.attempts == attempt and mail.delivery_status == "pending"
        assert email_svc.deliver_email(session, mail) == "failed"
        assert mail.attempts == email_svc.MAX_ATTEMPTS
        assert "SMTPServerDisconnected" in (mail.last_error or "")


def test_email_permanent_error_and_disabled_smtp(smtp_on, monkeypatch, client, admin, agent, requester, create_ticket):
    ticket = create_ticket(requester)
    client.patch(f"/tickets/{ticket['id']}", json={"assignee_id": agent.id}, headers=admin.headers)
    FakeSMTP.fail_with = smtplib.SMTPRecipientsRefused({"x@y": (550, b"no such user")})
    with SessionLocal() as session:
        mail = session.scalar(select(Notification).where(Notification.channel == "email"))
        assert email_svc.deliver_email(session, mail) == "failed"
        mail.delivery_status = "pending"
        monkeypatch.setattr(settings, "smtp_host", "")
        assert email_svc.deliver_email(session, mail) == "skipped"


def test_celery_task_retries_with_backoff(smtp_on, client, admin, agent, requester, create_ticket, monkeypatch):
    from app.tasks import notifications as tasks

    assert tasks.backoff_seconds(1) == 60 and tasks.backoff_seconds(3) == 240
    assert tasks.backoff_seconds(20) == tasks.BACKOFF_MAX_SECONDS
    ticket = create_ticket(requester)
    client.patch(f"/tickets/{ticket['id']}", json={"assignee_id": agent.id}, headers=admin.headers)
    with SessionLocal() as session:
        mail_id = session.scalar(select(Notification.id).where(Notification.channel == "email"))

    FakeSMTP.fail_with = smtplib.SMTPServerDisconnected("caído")
    retries: list[dict] = []

    def fake_retry(*args, **kwargs):
        retries.append(kwargs)
        raise RuntimeError("retry")

    monkeypatch.setattr(tasks.send_email, "retry", fake_retry)
    with pytest.raises(RuntimeError, match="retry"):
        tasks.send_email.run(mail_id)
    assert retries and retries[0]["countdown"] == 60
    with SessionLocal() as session:
        assert session.get(Notification, mail_id).attempts == 1  # persistido antes de reintentar

    FakeSMTP.fail_with = None
    assert tasks.send_email.run(mail_id) == "sent"
    assert tasks.send_email.run(999999) == "missing"


def test_stale_accounts_get_no_notifications_and_cannot_be_assigned(
    client, admin, agent, requester, create_ticket, monkeypatch
):
    """Una cuenta sin inicio de sesión reciente (roles/equipos sin verificar) queda fuera."""
    from datetime import timedelta

    from app.core.db import utcnow
    from app.models.core import User

    monkeypatch.setattr(settings, "account_stale_days", 30)
    ticket = create_ticket(requester)
    with SessionLocal() as session:
        session.get(User, agent.id).last_login_at = utcnow() - timedelta(days=60)
        session.commit()
    response = client.patch(f"/tickets/{ticket['id']}", json={"assignee_id": agent.id}, headers=admin.headers)
    assert response.status_code == 422
    assert "iniciado sesión" in response.json()["detail"]

    with SessionLocal() as session:
        ticket_row = session.get(Ticket, ticket["id"])
        created = notif_svc.notify(session, [agent.id, requester.id], kind="test", title="Aviso", ticket=ticket_row)
        session.commit()
        assert [n.user_id for n in created] == [requester.id]

    # Al volver a iniciar sesión vuelve a ser vigente.
    assert client.get("/me", headers=agent.headers).status_code == 200
    again = client.patch(f"/tickets/{ticket['id']}", json={"assignee_id": agent.id}, headers=admin.headers)
    assert again.status_code == 200, again.text
