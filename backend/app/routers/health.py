"""Salud del servicio."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.db import get_db

logger = logging.getLogger(__name__)

router = APIRouter(tags=["salud"])


@router.get("/health")
def health(response: Response, session: Session = Depends(get_db)) -> dict[str, str]:
    """Liveness + verificación de base de datos (503 si la BD no responde)."""
    try:
        session.execute(text("SELECT 1"))
    except Exception:
        logger.exception("La base de datos no responde")
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "error", "database": "error"}
    return {"status": "ok", "database": "ok"}
