"""Adjuntos de tickets: subida (multipart), listado, descarga proxied y borrado lógico.

Seguridad:
- Permisos verificados en el backend con las reglas de ``core/permissions.py``: ver el ticket
  para listar/descargar; subir solo el solicitante, el asignado o agentes del área (admin todo);
  borrar el autor del adjunto o quien puede editar el ticket.
- Límite de tamaño ``MAX_ATTACHMENT_MB`` (se rechaza por Content-Length antes de leer el cuerpo
  y se vuelve a comprobar al leerlo), lista blanca de extensiones/MIME y firma mágica.
- ``object_key`` aleatorio; el nombre original solo se guarda saneado.
- La descarga pasa por la API (streaming) con ``Content-Disposition: attachment``,
  ``X-Content-Type-Options: nosniff`` y CSP restrictiva; el bucket no se expone al navegador.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime, timedelta
from typing import Any, BinaryIO

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException

from app.core.config import settings
from app.core.db import get_db, utcnow
from app.core.events import emit
from app.core.permissions import can_edit_ticket
from app.core.security import Principal, get_principal
from app.models.attachments import TicketAttachment
from app.models.core import Ticket, WorkflowState
from app.services.storage import (
    ObjectNotFound,
    ObjectStorage,
    StorageError,
    get_storage,
)
from app.services.storage.validation import (
    ALLOWED_EXTENSIONS,
    FileValidationError,
    content_disposition,
    sanitize_filename,
    validate_file_type,
)
from app.services.tickets import get_visible_ticket

logger = logging.getLogger(__name__)

router = APIRouter(tags=["attachments"])

EVENT_ATTACHMENT_ADDED = "attachment.added"
EVENT_ATTACHMENT_DELETED = "attachment.deleted"

MAX_ATTACHMENTS_PER_TICKET = 50
# Margen para las cabeceras multipart al comparar Content-Length con el límite del archivo.
MULTIPART_OVERHEAD_BYTES = 64 * 1024
HEAD_BYTES = 8 * 1024
READ_CHUNK = 1024 * 1024
FILE_FIELD = "file"

STORAGE_UNAVAILABLE_DETAIL = (
    "El almacenamiento de adjuntos no está disponible en este momento. Intente más tarde."
)


# --------------------------------------------------------------------------- esquemas


class UserRefOut(BaseModel):
    id: int
    display_name: str


class AttachmentOut(BaseModel):
    id: int
    ticket_id: int
    filename: str
    size: int
    mime: str
    checksum: str
    uploaded_by: UserRefOut
    created_at: datetime
    can_delete: bool


class AttachmentSettingsOut(BaseModel):
    max_mb: int
    max_bytes: int
    max_per_ticket: int
    allowed_extensions: list[str]


# --------------------------------------------------------------------------- utilidades


def max_attachment_bytes() -> int:
    return settings.max_attachment_mb * 1024 * 1024


def _too_large() -> HTTPException:
    return HTTPException(
        status.HTTP_413_CONTENT_TOO_LARGE,
        detail=f"El archivo excede el tamaño máximo permitido ({settings.max_attachment_mb} MB)",
    )


def _storage_unavailable() -> HTTPException:
    return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=STORAGE_UNAVAILABLE_DETAIL)


def _attachment_not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, detail="Adjunto no encontrado")


def can_upload(principal: Principal, ticket: Ticket) -> bool:
    """Solicitante y asignado del ticket, agentes de su área y administradores."""
    return principal.user_id in (ticket.requester_id, ticket.assignee_id) or can_edit_ticket(
        principal, ticket
    )


def can_delete(principal: Principal, ticket: Ticket, attachment: TicketAttachment) -> bool:
    """El autor del adjunto o quien puede editar el ticket (agente del área / admin)."""
    return attachment.uploaded_by == principal.user_id or can_edit_ticket(principal, ticket)


def serialize_attachment(
    attachment: TicketAttachment, principal: Principal, ticket: Ticket
) -> dict[str, Any]:
    uploader = attachment.uploader
    return {
        "id": attachment.id,
        "ticket_id": attachment.ticket_id,
        "filename": attachment.filename,
        "size": attachment.size,
        "mime": attachment.mime,
        "checksum": attachment.checksum,
        "uploaded_by": {
            "id": attachment.uploaded_by,
            "display_name": uploader.display_name if uploader else "—",
        },
        "created_at": attachment.created_at,
        "can_delete": can_delete(principal, ticket, attachment),
    }


def _active_attachment(session: Session, attachment_id: int) -> TicketAttachment:
    attachment = session.scalars(
        select(TicketAttachment)
        .options(joinedload(TicketAttachment.uploader))
        .where(TicketAttachment.id == attachment_id, TicketAttachment.deleted_at.is_(None))
    ).first()
    if attachment is None:
        raise _attachment_not_found()
    return attachment


def _user_upload_usage(session: Session, user_id: int) -> tuple[int, int]:
    """(nº de adjuntos, bytes) subidos por el usuario en las últimas 24 h (incluye borrados)."""
    since = utcnow() - timedelta(hours=24)
    count, total = session.execute(
        select(func.count(TicketAttachment.id), func.coalesce(func.sum(TicketAttachment.size), 0)).where(
            TicketAttachment.uploaded_by == user_id, TicketAttachment.created_at >= since
        )
    ).one()
    return int(count or 0), int(total or 0)


def _quota_exceeded(detail: str) -> HTTPException:
    return HTTPException(
        status.HTTP_429_TOO_MANY_REQUESTS, detail=detail, headers={"Retry-After": "3600"}
    )


def _check_user_quota(session: Session, principal: Principal, incoming_bytes: int = 0) -> None:
    """Cuota diaria por usuario (``ATTACHMENT_USER_DAILY_COUNT`` / ``_MB``); admin exento."""
    if principal.is_admin:
        return
    max_count = settings.attachment_user_daily_count
    max_bytes = settings.attachment_user_daily_mb * 1024 * 1024
    if not max_count and not max_bytes:
        return
    count, used = _user_upload_usage(session, principal.user_id)
    if max_count and incoming_bytes == 0 and count >= max_count:
        raise _quota_exceeded(
            f"Alcanzó el máximo de {max_count} adjuntos en 24 horas. Intente más tarde."
        )
    if max_bytes and used + incoming_bytes > max_bytes:
        raise _quota_exceeded(
            f"Superaría su cuota de {settings.attachment_user_daily_mb} MB de adjuntos en 24 horas."
        )


def _check_upload_allowed(session: Session, principal: Principal, ticket_id: int) -> Ticket:
    ticket = get_visible_ticket(session, principal, ticket_id)
    if not can_upload(principal, ticket):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, detail="No tiene permisos para adjuntar archivos a este ticket"
        )
    state = session.get(WorkflowState, ticket.status_id)
    if state is not None and state.is_final:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="No se pueden adjuntar archivos a un ticket cerrado"
        )
    count = session.scalar(
        select(func.count())
        .select_from(TicketAttachment)
        .where(TicketAttachment.ticket_id == ticket.id, TicketAttachment.deleted_at.is_(None))
    )
    if (count or 0) >= MAX_ATTACHMENTS_PER_TICKET:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"El ticket alcanzó el máximo de {MAX_ATTACHMENTS_PER_TICKET} adjuntos",
        )
    _check_user_quota(session, principal)
    return ticket


def _digest(fileobj: BinaryIO, limit: int) -> tuple[bytes, int, str]:
    """Lee el archivo una vez: primeros bytes, tamaño y SHA-256. Lanza 413 si excede el límite."""
    fileobj.seek(0)
    sha = hashlib.sha256()
    head = b""
    size = 0
    while True:
        chunk = fileobj.read(READ_CHUNK)
        if not chunk:
            break
        if len(head) < HEAD_BYTES:
            head += chunk[: HEAD_BYTES - len(head)]
        size += len(chunk)
        if size > limit:
            raise _too_large()
        sha.update(chunk)
    fileobj.seek(0)
    return head, size, sha.hexdigest()


def _store_attachment(
    session: Session,
    storage: ObjectStorage,
    principal: Principal,
    ticket: Ticket,
    upload: UploadFile,
) -> dict[str, Any]:
    filename = sanitize_filename(upload.filename)
    head, size, checksum = _digest(upload.file, max_attachment_bytes())
    _check_user_quota(session, principal, incoming_bytes=max(size, 1))
    try:
        rule = validate_file_type(filename, upload.content_type, head)
    except FileValidationError as exc:
        raise HTTPException(exc.status_code, detail=str(exc)) from exc

    object_key = f"tickets/{ticket.id}/{uuid.uuid4().hex}"
    try:
        storage.put_object(
            object_key,
            upload.file,
            size=size,
            content_type=rule.mime,
            metadata={"sha256": checksum, "ticket-id": str(ticket.id)},
        )
    except StorageError as exc:
        logger.error("No se pudo guardar el adjunto del ticket %s: %s", ticket.id, exc)
        raise _storage_unavailable() from exc

    try:
        attachment = TicketAttachment(
            ticket_id=ticket.id,
            object_key=object_key,
            filename=filename,
            size=size,
            mime=rule.mime,
            checksum=checksum,
            uploaded_by=principal.user_id,
            created_at=utcnow(),
        )
        session.add(attachment)
        session.flush()
        emit(
            session,
            EVENT_ATTACHMENT_ADDED,
            ticket,
            principal.user_id,
            {
                "attachment_id": attachment.id,
                "filename": filename,
                "size": size,
                "mime": rule.mime,
            },
        )
        session.commit()
    except Exception:
        session.rollback()
        try:
            storage.delete_object(object_key)
        except StorageError:  # pragma: no cover - limpieza best-effort
            logger.warning("Objeto huérfano en el almacenamiento: %s", object_key)
        raise

    session.refresh(attachment)
    logger.info(
        "Adjunto %s (%s, %s bytes) agregado al ticket %s por el usuario %s",
        attachment.id, rule.mime, size, ticket.key, principal.user_id,
    )
    return serialize_attachment(attachment, principal, ticket)


# --------------------------------------------------------------------------- endpoints


@router.get("/attachments/settings", response_model=AttachmentSettingsOut)
def attachment_settings(_: Principal = Depends(get_principal)) -> dict[str, Any]:
    """Límites vigentes (para mostrar ayuda en la interfaz)."""
    return {
        "max_mb": settings.max_attachment_mb,
        "max_bytes": max_attachment_bytes(),
        "max_per_ticket": MAX_ATTACHMENTS_PER_TICKET,
        "allowed_extensions": list(ALLOWED_EXTENSIONS),
    }


@router.get("/tickets/{ticket_id}/attachments", response_model=list[AttachmentOut])
def list_attachments(
    ticket_id: int,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    ticket = get_visible_ticket(session, principal, ticket_id)
    attachments = session.scalars(
        select(TicketAttachment)
        .options(joinedload(TicketAttachment.uploader))
        .where(TicketAttachment.ticket_id == ticket.id, TicketAttachment.deleted_at.is_(None))
        .order_by(TicketAttachment.created_at, TicketAttachment.id)
    ).all()
    return [serialize_attachment(a, principal, ticket) for a in attachments]


@router.post(
    "/tickets/{ticket_id}/attachments",
    response_model=AttachmentOut,
    status_code=status.HTTP_201_CREATED,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "required": [FILE_FIELD],
                        "properties": {FILE_FIELD: {"type": "string", "format": "binary"}},
                    }
                }
            },
        }
    },
)
async def upload_attachment(
    ticket_id: int,
    request: Request,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
    storage: ObjectStorage = Depends(get_storage),
) -> dict[str, Any]:
    """Sube un archivo (campo ``file``) al ticket. Emite ``attachment.added``."""
    limit = max_attachment_bytes()
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared_length = int(content_length)
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Content-Length inválido") from None
        if declared_length > limit + MULTIPART_OVERHEAD_BYTES:
            raise _too_large()

    # Permisos antes de aceptar el cuerpo: no se procesan archivos de quien no puede adjuntar.
    ticket = await run_in_threadpool(_check_upload_allowed, session, principal, ticket_id)

    content_type = request.headers.get("content-type", "")
    if not content_type.lower().startswith("multipart/form-data"):
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Envíe el archivo como multipart/form-data en el campo 'file'",
        )
    try:
        form = await request.form(max_files=1, max_fields=5)
    except MultiPartException as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"Formulario inválido: {exc.message}") from exc

    try:
        upload = form.get(FILE_FIELD)
        if not isinstance(upload, UploadFile):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Falta el archivo en el campo 'file'"
            )
        if upload.size is not None and upload.size > limit:
            raise _too_large()
        return await run_in_threadpool(_store_attachment, session, storage, principal, ticket, upload)
    finally:
        await form.close()


@router.get(
    "/attachments/{attachment_id}/download",
    response_class=StreamingResponse,
    responses={200: {"content": {"application/octet-stream": {}}}},
)
def download_attachment(
    attachment_id: int,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
    storage: ObjectStorage = Depends(get_storage),
) -> StreamingResponse:
    """Descarga el archivo (streaming) si el usuario puede ver el ticket."""
    attachment = _active_attachment(session, attachment_id)
    try:
        get_visible_ticket(session, principal, attachment.ticket_id)
    except HTTPException as exc:
        # No revelar la existencia de adjuntos de tickets no visibles.
        raise _attachment_not_found() from exc

    try:
        stored = storage.open_object(attachment.object_key)
    except ObjectNotFound as exc:
        logger.error(
            "El adjunto %s no existe en el almacenamiento (key=%s)", attachment.id, attachment.object_key
        )
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail="El archivo ya no está disponible en el almacenamiento"
        ) from exc
    except StorageError as exc:
        raise _storage_unavailable() from exc

    headers = {
        "Content-Disposition": content_disposition(attachment.filename),
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; sandbox",
        "Cache-Control": "private, no-store",
        "X-Checksum-SHA256": attachment.checksum,
    }
    size = stored.size if stored.size is not None else attachment.size
    headers["Content-Length"] = str(size)
    return StreamingResponse(
        stored.iter_and_close(), media_type=attachment.mime or "application/octet-stream", headers=headers
    )


@router.delete("/attachments/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_attachment(
    attachment_id: int,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> Response:
    """Borrado lógico (el objeto se conserva para auditoría). Emite ``attachment.deleted``."""
    attachment = _active_attachment(session, attachment_id)
    try:
        ticket = get_visible_ticket(session, principal, attachment.ticket_id)
    except HTTPException as exc:
        raise _attachment_not_found() from exc
    if not can_delete(principal, ticket, attachment):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="Solo el autor del adjunto o un agente del área pueden eliminarlo",
        )
    attachment.deleted_at = utcnow()
    emit(
        session,
        EVENT_ATTACHMENT_DELETED,
        ticket,
        principal.user_id,
        {"attachment_id": attachment.id, "filename": attachment.filename},
    )
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)

