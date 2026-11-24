"""Tareas Celery de notificaciones por correo.

- ``notifications.send_email(notification_id)``: envía un correo (fila ``channel='email'``)
  con reintentos y backoff exponencial (60 s, 120 s, 240 s...; máx. ``MAX_ATTEMPTS``).
- ``notifications.retry_pending``: barrido periódico (beat) que reencola correos pendientes
  que no llegaron al broker (p.ej. Redis caído al momento del commit).
"""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import select

from app.core.db import SessionLocal, utcnow
from app.models.automation import Notification
from app.services.notifications.email import MAX_ATTEMPTS, deliver_email  # noqa: F401
from app.tasks import celery_app

logger = logging.getLogger(__name__)

BACKOFF_BASE_SECONDS = 60
BACKOFF_MAX_SECONDS = 3600
STALE_PENDING_MINUTES = 10
SWEEP_BATCH = 200


def backoff_seconds(attempt: int) -> int:
    """Segundos de espera antes del siguiente intento (exponencial con tope)."""
    return min(BACKOFF_MAX_SECONDS, BACKOFF_BASE_SECONDS * (2 ** max(0, attempt - 1)))


@celery_app.task(name="notifications.send_email", bind=True, max_retries=MAX_ATTEMPTS)
def send_email(self, notification_id: int) -> str:
    with SessionLocal() as session:
        notification = session.get(Notification, notification_id, with_for_update=True)
        if notification is None or notification.channel != "email":
            logger.warning("Notificación de correo %s inexistente", notification_id)
            return "missing"
        try:
            status = deliver_email(session, notification)
        except Exception as exc:
            attempts = notification.attempts
            session.commit()  # persiste attempts/last_error antes de reintentar
            raise self.retry(exc=exc, countdown=backoff_seconds(attempts))
        session.commit()
        return status


@celery_app.task(name="notifications.retry_pending")
def retry_pending() -> int:
    """Reencola correos ``pending`` antiguos que no se enviaron (broker caído, worker reiniciado)."""
    threshold = utcnow() - timedelta(minutes=STALE_PENDING_MINUTES)
    with SessionLocal() as session:
        ids = session.scalars(
            select(Notification.id)
            .where(
                Notification.channel == "email",
                Notification.delivery_status == "pending",
                # Solo los que nunca se intentaron: los que fallaron siguen el ciclo de
                # reintentos de Celery (evita envíos duplicados).
                Notification.attempts == 0,
                Notification.created_at < threshold,
            )
            .order_by(Notification.id)
            .limit(SWEEP_BATCH)
        ).all()
    for notification_id in ids:
        send_email.apply_async(args=[notification_id])
    if ids:
        logger.info("Reencolados %d correos pendientes", len(ids))
    return len(ids)


beat_schedule = {
    "notifications-retry-pending": {"task": "notifications.retry_pending", "schedule": 300.0},
}
