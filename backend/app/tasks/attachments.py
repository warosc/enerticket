"""Tareas Celery de adjuntos.

``attachments.purge_deleted`` (diaria) elimina de S3/MinIO el contenido de los adjuntos
borrados lógicamente hace más de ``ATTACHMENT_PURGE_DAYS`` días y luego su fila de
metadatos (el historial del ticket conserva el evento ``attachment.deleted`` con el nombre).
Con ``ATTACHMENT_PURGE_DAYS=0`` no se purga nada.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import SessionLocal, utcnow
from app.models.attachments import TicketAttachment
from app.services.storage import get_storage
from app.services.storage.base import ObjectNotFound, ObjectStorage, StorageError
from app.tasks import celery_app

logger = logging.getLogger(__name__)

BATCH_SIZE = 200


def purge_deleted_attachments(
    *,
    days: int | None = None,
    storage: ObjectStorage | None = None,
    session_factory: Callable[[], Session] = SessionLocal,
    batch_size: int = BATCH_SIZE,
) -> dict[str, int]:
    """Purga adjuntos borrados hace más de ``days`` días. Devuelve contadores."""
    days = settings.attachment_purge_days if days is None else days
    stats = {"purged": 0, "missing": 0, "errors": 0}
    if days <= 0:
        return stats
    cutoff = utcnow() - timedelta(days=days)
    storage = storage or get_storage()
    with session_factory() as session:
        rows = session.scalars(
            select(TicketAttachment)
            .where(TicketAttachment.deleted_at.is_not(None), TicketAttachment.deleted_at < cutoff)
            .order_by(TicketAttachment.id)
            .limit(batch_size)
        ).all()
        for attachment in rows:
            try:
                storage.delete_object(attachment.object_key)
            except ObjectNotFound:
                stats["missing"] += 1
            except StorageError as exc:
                # Se reintenta en la próxima ejecución; la fila se conserva.
                stats["errors"] += 1
                logger.warning("No se pudo purgar el adjunto %s: %s", attachment.id, exc)
                continue
            session.delete(attachment)
            stats["purged"] += 1
        session.commit()
    if stats["purged"] or stats["errors"]:
        logger.info("Purga de adjuntos borrados: %s", stats)
    return stats


@celery_app.task(name="attachments.purge_deleted", ignore_result=True)
def purge_deleted_task() -> dict[str, int]:
    return purge_deleted_attachments()


beat_schedule = {
    "attachments-purge-deleted": {
        "task": "attachments.purge_deleted",
        "schedule": 6 * 3600.0,
        "options": {"expires": 3600},
    },
}
