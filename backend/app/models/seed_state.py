"""Registro de seeds de «una sola vez» (``seed_state``).

Los seeds corren en cada arranque del contenedor ``api``. Los datos de ejemplo o de
configuración inicial que un administrador puede editar, renombrar o borrar (departamentos,
calendarios y políticas SLA, políticas de aprobación y reglas de ejemplo) se siembran una
única vez: la marca queda aquí y los arranques siguientes ya no los recrean.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TZDateTime


class SeedState(Base):
    __tablename__ = "seed_state"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    applied_at: Mapped[datetime] = mapped_column(TZDateTime(), nullable=False)
    data: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
