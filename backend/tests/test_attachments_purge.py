"""Purga periódica de adjuntos borrados lógicamente (tarea attachments.purge_deleted)."""

from __future__ import annotations

import io
from datetime import timedelta

from sqlalchemy import select

from app.core.db import utcnow
from app.models.attachments import TicketAttachment
from app.services.storage.base import StorageUnavailable
from app.services.storage.memory import InMemoryStorage
from app.tasks import celery_app
from app.tasks.attachments import purge_deleted_attachments


def _add(db, ticket_id, user_id, key, deleted_days_ago=None):
    row = TicketAttachment(
        ticket_id=ticket_id, object_key=key, filename=f"{key}.txt", size=3, mime="text/plain",
        checksum="0" * 64, uploaded_by=user_id, created_at=utcnow() - timedelta(days=90),
        deleted_at=None if deleted_days_ago is None else utcnow() - timedelta(days=deleted_days_ago),
    )
    db.add(row)
    db.commit()
    return row.id


def test_purge_removes_old_deleted_objects_only(db, requester, create_ticket):
    ticket = create_ticket(requester)
    storage = InMemoryStorage()
    for key in ("active", "recent", "old", "old-missing"):
        if key == "old-missing":
            continue
        storage.put_object(key, io.BytesIO(b"abc"), size=3, content_type="text/plain")
    active = _add(db, ticket["id"], requester.id, "active")
    recent = _add(db, ticket["id"], requester.id, "recent", deleted_days_ago=5)
    old = _add(db, ticket["id"], requester.id, "old", deleted_days_ago=45)
    missing = _add(db, ticket["id"], requester.id, "old-missing", deleted_days_ago=60)

    stats = purge_deleted_attachments(days=30, storage=storage)

    assert stats == {"purged": 2, "missing": 0, "errors": 0}
    db.expire_all()
    remaining = set(db.scalars(select(TicketAttachment.id)).all())
    assert remaining == {active, recent}
    assert old not in remaining and missing not in remaining
    assert "active" in storage.objects and "recent" in storage.objects and "old" not in storage.objects


def test_purge_disabled_with_zero_days(db, requester, create_ticket):
    ticket = create_ticket(requester)
    _add(db, ticket["id"], requester.id, "old", deleted_days_ago=400)
    assert purge_deleted_attachments(days=0, storage=InMemoryStorage()) == {"purged": 0, "missing": 0, "errors": 0}
    assert db.scalar(select(TicketAttachment.id)) is not None


def test_purge_keeps_row_when_storage_fails(db, requester, create_ticket):
    ticket = create_ticket(requester)
    row_id = _add(db, ticket["id"], requester.id, "old", deleted_days_ago=45)

    class Broken(InMemoryStorage):
        def delete_object(self, key):
            raise StorageUnavailable("sin conexión")

    assert purge_deleted_attachments(days=30, storage=Broken())["errors"] == 1
    db.expire_all()
    assert db.get(TicketAttachment, row_id) is not None


def test_purge_task_registered_in_beat():
    entries = {v["task"] for v in celery_app.conf.beat_schedule.values()}
    assert "attachments.purge_deleted" in entries
