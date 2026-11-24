"""Plantillas (español) y envío SMTP de notificaciones por correo.

``deliver_email(session, notification)`` envía una fila ``channel='email'`` y actualiza su
estado (``sent`` | ``failed`` | ``skipped``). La tarea Celery ``notifications.send_email``
se encarga de los reintentos con backoff.
"""

from __future__ import annotations

import html
import logging
import smtplib
import ssl
from collections.abc import Callable
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import utcnow
from app.models.automation import Notification
from app.models.core import User
from app.services.notifications import smtp_enabled, ticket_url

logger = logging.getLogger(__name__)

SMTP_TIMEOUT_SECONDS = 20
MAX_ATTEMPTS = 5

KIND_HEADINGS: dict[str, str] = {
    "ticket_assigned": "Ticket asignado",
    "ticket_commented": "Nuevo comentario",
    "ticket_resolved": "Ticket resuelto",
    "approval_requested": "Aprobación requerida",
    "approval_decided": "Decisión de aprobación",
    "sla_warning": "SLA en riesgo",
    "sla_breached": "SLA incumplido",
    "automation": "Notificación automática",
}

SmtpFactory = Callable[..., Any]


class PermanentEmailError(Exception):
    """Error que no se resuelve reintentando (destinatario inválido, configuración...)."""


def render_email(notification: Notification, user: User) -> tuple[str, str, str]:
    """Devuelve (asunto, texto plano, HTML) para la notificación."""
    heading = KIND_HEADINGS.get(notification.kind, "Notificación")
    subject = f"[Service Desk] {notification.title}"
    link = ticket_url(notification.ticket_id) if notification.ticket_id else None
    greeting = f"Hola {user.display_name or user.username}:"
    body = notification.body or ""

    text_lines = [greeting, "", notification.title]
    if body:
        text_lines += ["", body]
    if link:
        text_lines += ["", f"Ver el ticket: {link}"]
    text_lines += [
        "",
        "—",
        "Energuate Service Desk. Este es un mensaje automático; no responda a este correo.",
    ]
    text = "\n".join(text_lines)

    esc = html.escape
    body_html = "<br>".join(esc(line) for line in body.splitlines()) if body else ""
    button = (
        f'<p style="margin:24px 0 0"><a href="{esc(link, quote=True)}" '
        'style="background:#1559a3;color:#ffffff;padding:10px 18px;border-radius:6px;'
        'text-decoration:none;font-weight:600;display:inline-block">Ver ticket</a></p>'
        if link
        else ""
    )
    html_doc = f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8"><title>{esc(subject)}</title></head>
<body style="margin:0;padding:0;background:#f3f5f9;font-family:Segoe UI,Roboto,Arial,sans-serif;color:#14243a">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f3f5f9;padding:24px 0">
    <tr><td align="center">
      <table role="presentation" width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;background:#ffffff;border:1px solid #dde3ec;border-radius:10px">
        <tr><td style="background:#0e2a4a;color:#ffffff;padding:16px 24px;border-radius:10px 10px 0 0;font-weight:700">
          Energuate Service Desk
        </td></tr>
        <tr><td style="padding:24px">
          <p style="margin:0 0 4px;font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:#56677d">{esc(heading)}</p>
          <h1 style="margin:0 0 16px;font-size:20px;line-height:1.3">{esc(notification.title)}</h1>
          <p style="margin:0 0 12px">{esc(greeting)}</p>
          {f'<p style="margin:0;line-height:1.5">{body_html}</p>' if body_html else ''}
          {button}
        </td></tr>
        <tr><td style="padding:16px 24px;border-top:1px solid #dde3ec;font-size:12px;color:#7b8a9e">
          Mensaje automático del Service Desk de Energuate. No responda a este correo.
        </td></tr>
      </table>
    </td></tr>
  </table>
</body></html>"""
    return subject, text, html_doc


def build_message(notification: Notification, user: User) -> EmailMessage:
    if not user.email:
        raise PermanentEmailError("El usuario no tiene correo electrónico")
    subject, text, html_doc = render_email(notification, user)
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = formataddr(("Energuate Service Desk", settings.smtp_from))
    message["To"] = formataddr((user.display_name or user.username, user.email))
    message["Message-ID"] = make_msgid(domain=settings.smtp_from.rsplit("@", 1)[-1] or None)
    message["Auto-Submitted"] = "auto-generated"
    message.set_content(text)
    message.add_alternative(html_doc, subtype="html")
    return message


def _default_smtp_factory() -> Any:
    host = settings.smtp_host.strip()
    port = settings.smtp_port
    context = ssl.create_default_context()
    if port == 465:
        return smtplib.SMTP_SSL(host, port, timeout=SMTP_TIMEOUT_SECONDS, context=context)
    client = smtplib.SMTP(host, port, timeout=SMTP_TIMEOUT_SECONDS)
    if settings.smtp_starttls:
        client.starttls(context=context)
    return client


_smtp_factory: SmtpFactory = _default_smtp_factory


def set_smtp_factory(factory: SmtpFactory | None) -> None:
    """Reemplaza la fábrica de clientes SMTP (pruebas). ``None`` restaura smtplib."""
    global _smtp_factory
    _smtp_factory = factory or _default_smtp_factory


def send_message(message: EmailMessage) -> None:
    client = _smtp_factory()
    try:
        if settings.smtp_user:
            client.login(settings.smtp_user, settings.smtp_password)
        client.send_message(message)
    finally:
        try:
            client.quit()
        except Exception:  # pragma: no cover - cierre best effort
            pass


def deliver_email(session: Session, notification: Notification) -> str:
    """Envía el correo y actualiza el estado. Devuelve el estado final.

    Lanza la excepción original si el envío falló y aún quedan reintentos (``attempts`` ya
    incrementado); el llamador decide reintentar. Los errores permanentes marcan ``failed``.
    """
    if notification.channel != "email":
        raise ValueError("La notificación no es de correo")
    if notification.delivery_status in ("sent", "skipped"):
        return notification.delivery_status
    if not smtp_enabled():
        notification.delivery_status = "skipped"
        notification.last_error = "SMTP_HOST no configurado"
        logger.info("SMTP_HOST vacío: correo %s no enviado (%s)", notification.id, notification.title)
        return "skipped"
    user = session.get(User, notification.user_id)
    if user is None or not user.active:
        notification.delivery_status = "skipped"
        notification.last_error = "Usuario inexistente o inactivo"
        return "skipped"
    notification.attempts = (notification.attempts or 0) + 1
    try:
        message = build_message(notification, user)
        send_message(message)
    except (PermanentEmailError, smtplib.SMTPRecipientsRefused) as exc:
        notification.delivery_status = "failed"
        notification.last_error = str(exc)[:2000]
        logger.warning("Correo %s descartado: %s", notification.id, exc)
        return "failed"
    except Exception as exc:
        notification.last_error = f"{type(exc).__name__}: {exc}"[:2000]
        if notification.attempts >= MAX_ATTEMPTS:
            notification.delivery_status = "failed"
            logger.error(
                "Correo %s falló tras %d intentos: %s", notification.id, notification.attempts, exc
            )
            return "failed"
        logger.warning(
            "Correo %s falló (intento %d/%d): %s",
            notification.id,
            notification.attempts,
            MAX_ATTEMPTS,
            exc,
        )
        raise
    notification.delivery_status = "sent"
    notification.sent_at = utcnow()
    notification.last_error = None
    return "sent"
