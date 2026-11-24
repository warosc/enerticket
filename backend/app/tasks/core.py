"""Tareas de la fundación."""

from __future__ import annotations

from sqlalchemy import text

from app.core.db import SessionLocal, utcnow
from app.tasks import celery_app


@celery_app.task(name="core.ping")
def ping() -> dict[str, str]:
    """Comprueba que el worker procesa tareas y alcanza la base de datos."""
    with SessionLocal() as session:
        session.execute(text("SELECT 1"))
    return {"status": "ok", "ts": utcnow().isoformat()}
